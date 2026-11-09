using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.Text;

/// <summary>Resolve pinned C# syntax against metadata references without emitting or running project code.</summary>
internal static class Worker
{
    private const int MaxInputBytes = 4 * 1024 * 1024;
    private const int MaxQueries = 64;
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    /// <summary>Probe real compiler loading or analyze one bounded JSON request on standard input.</summary>
    public static int Run(bool probe)
    {
        if (probe)
        {
            using var check = JsonDocument.Parse(ReadInput());
            var options = check.RootElement;
            if (!LanguageVersionFacts.TryParse(options.GetProperty("languageVersion").GetString(), out _))
                throw new ArgumentException("unsupported-language-version");
            var probeReferences = LoadReferences(options.GetProperty("references"));
            var probeCompilation = CSharpCompilation.Create("AtlasReferenceProbe", references: probeReferences);
            if (probeCompilation.GetSpecialType(SpecialType.System_Object).TypeKind == TypeKind.Error)
                throw new ArgumentException("reference-pack-incompatible");
            Console.WriteLine(JsonSerializer.Serialize(new { version = typeof(CSharpCompilation).Assembly.GetName().Version?.ToString(), protocol = "atlas-roslyn-v1" }));
            return 0;
        }
        using var document = JsonDocument.Parse(ReadInput(), new JsonDocumentOptions { MaxDepth = 32 });
        var input = document.RootElement;
        if (input.GetProperty("protocol").GetString() != "atlas-roslyn-v1") throw new ArgumentException("protocol");
        var files = input.GetProperty("files");
        var queries = input.GetProperty("queries");
        var referencePaths = input.GetProperty("references");
        if (files.GetArrayLength() > 16 || queries.GetArrayLength() > MaxQueries || referencePaths.GetArrayLength() > 512)
            throw new ArgumentException("inventory-budget");
        if (!LanguageVersionFacts.TryParse(input.GetProperty("languageVersion").GetString(), out var languageVersion))
            throw new ArgumentException("language-version");
        var defines = input.GetProperty("defines").EnumerateArray().Select(value => value.GetString()!).ToArray();
        var parseOptions = new CSharpParseOptions(languageVersion, preprocessorSymbols: defines);
        var trees = new Dictionary<string, SyntaxTree>(StringComparer.Ordinal);
        foreach (var file in files.EnumerateArray())
        {
            string path = file.GetProperty("path").GetString()!;
            string text = file.GetProperty("text").GetString()!;
            if (!SafeRelativePath(path) || trees.ContainsKey(path)) throw new ArgumentException("source-path");
            trees[path] = CSharpSyntaxTree.ParseText(text, parseOptions, path, Encoding.UTF8);
        }
        var references = LoadReferences(referencePaths);
        var compilation = CSharpCompilation.Create("AtlasCapturedSources", trees.Values, references,
            new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: false));
        bool compilationHasErrors = compilation.GetDiagnostics().Any(diagnostic => diagnostic.Severity == DiagnosticSeverity.Error);
        var models = trees.ToDictionary(pair => pair.Key, pair => compilation.GetSemanticModel(pair.Value));
        var results = new List<object>();
        foreach (var query in queries.EnumerateArray())
            results.Add(ResolveQuery(query, trees, models));
        Console.WriteLine(JsonSerializer.Serialize(new { protocol = "atlas-roslyn-v1", compilationHasErrors, results }, JsonOptions));
        return 0;
    }

    /// <summary>Bound direct bridge input independently of the host's process transport.</summary>
    private static byte[] ReadInput()
    {
        using var stream = Console.OpenStandardInput();
        using var output = new MemoryStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = stream.Read(buffer, 0, buffer.Length)) > 0)
        {
            if (output.Length + count > MaxInputBytes) throw new ArgumentException("input-budget");
            output.Write(buffer, 0, count);
        }
        return output.ToArray();
    }

    /// <summary>Allow only normalized graph-owned filenames; the worker never reads these source paths.</summary>
    private static bool SafeRelativePath(string path) => !Path.IsPathRooted(path) && !path.Contains('\\')
        && !path.Contains(':') && path.Split('/').All(part => part.Length > 0 && part != "." && part != "..");

    /// <summary>Read bounded reference metadata without Assembly.Load or analyzer/generator execution.</summary>
    private static List<MetadataReference> LoadReferences(JsonElement paths)
    {
        if (paths.GetArrayLength() > 512) throw new ArgumentException("reference-budget");
        var references = new List<MetadataReference>();
        foreach (var path in paths.EnumerateArray())
        {
            string location = path.GetString()!;
            if (!Path.IsPathFullyQualified(location)) throw new ArgumentException("reference-path");
            references.Add(MetadataReference.CreateFromFile(location));
        }
        return references;
    }

    /// <summary>Require an exact original expression and its enclosing selected source declaration.</summary>
    private static object ResolveQuery(JsonElement query, Dictionary<string, SyntaxTree> trees,
        Dictionary<string, SemanticModel> models)
    {
        string path = query.GetProperty("path").GetString()!;
        if (!trees.TryGetValue(path, out var tree)) return Unknown("source-not-pinned");
        var text = tree.GetText();
        if (!TrySpan(query, text, out var requested)) return Unknown("invalid-query-span");
        if (!TrySpan(query.GetProperty("sourceSpan"), text, out var ownerSpan)) return Unknown("invalid-owner-span");
        var root = tree.GetRoot();
        var owner = ExactNode(root, ownerSpan, IsDeclaration);
        if (owner is null || !owner.Span.Contains(requested)) return Unknown("owner-not-exact");
        var syntax = ExactNode(root, requested, node => node is InvocationExpressionSyntax
            or ObjectCreationExpressionSyntax or ImplicitObjectCreationExpressionSyntax
            or ConstructorInitializerSyntax or IdentifierNameSyntax or GenericNameSyntax or MemberAccessExpressionSyntax);
        if (syntax is null || syntax.ContainsDiagnostics) return Unknown("query-not-exact");
        if (syntax.Ancestors().TakeWhile(node => node != owner).Any(IsDeclaration))
            return Unknown("query-owned-by-deferred-declaration");
        var model = models[path];
        if (syntax is InvocationExpressionSyntax invocation && invocation.Expression is IdentifierNameSyntax name
            && name.Identifier.ValueText == "nameof" && model.GetConstantValue(invocation).HasValue)
            return new { status = "language-construct", reason = "nameof-not-runtime-call" };
        var info = model.GetSymbolInfo(syntax);
        var symbol = info.Symbol;
        if (symbol is null || info.CandidateReason != CandidateReason.None)
            return Unknown(info.CandidateSymbols.Length > 0 ? "ambiguous-or-invalid-binding" : "unresolved-project-or-reference");
        if (model.GetDiagnostics(requested).Any(diagnostic => diagnostic.Severity == DiagnosticSeverity.Error))
            return Unknown("binding-has-source-errors");
        var canonical = symbol is IMethodSymbol method ? method.ReducedFrom ?? method.OriginalDefinition : symbol.OriginalDefinition;
        var locations = canonical.Locations.Where(location => location.IsInSource).ToArray();
        object? location = null;
        if (locations.Length == 1 && locations[0].SourceTree is SyntaxTree destination && trees.ContainsKey(destination.FilePath))
            location = DescribeSpan(destination.FilePath, destination.GetText(), locations[0].SourceSpan);
        string origin = locations.Length > 0 ? "project-source" : "metadata";
        string display = canonical.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat);
        string symbolId = canonical.GetDocumentationCommentId() ?? display;
        if (symbolId.Length > 2048) return Unknown("symbol-identity-budget");
        return new
        {
            status = "confirmed-static", symbolId,
            label = Truncate(display, 400), kind = canonical.Kind.ToString().ToLowerInvariant(),
            location, origin, assembly = canonical.ContainingAssembly?.Name,
            receiverType = ReceiverType(syntax, model),
            extensionMethod = symbol is IMethodSymbol selected && selected.ReducedFrom is not null,
            runtimeSelectionKnown = false,
        };
    }

    /// <summary>Resolve exact spans without trusting a neighboring expression at the same start point.</summary>
    private static SyntaxNode? ExactNode(SyntaxNode root, TextSpan span, Func<SyntaxNode, bool> accepts)
    {
        var current = root.FindNode(span, getInnermostNodeForTie: true);
        for (int depth = 0; current is not null && depth < 256; depth++, current = current.Parent)
        {
            if (current.Span == span && accepts(current)) return current;
            if (!span.Contains(current.Span)) break;
        }
        return null;
    }

    /// <summary>Keep local functions/lambdas as independent owners rather than unconditional outer work.</summary>
    private static bool IsDeclaration(SyntaxNode node) => node is BaseMethodDeclarationSyntax
        or LocalFunctionStatementSyntax or AnonymousFunctionExpressionSyntax;

    /// <summary>Read zero-based UTF-16 coordinates only at valid original source positions.</summary>
    private static bool TrySpan(JsonElement value, SourceText text, out TextSpan span)
    {
        span = default;
        try
        {
            int line = value.GetProperty("line").GetInt32();
            int column = value.GetProperty("column").GetInt32();
            int endLine = value.GetProperty("endLine").GetInt32();
            int endColumn = value.GetProperty("endColumn").GetInt32();
            if (line < 0 || endLine < line || endLine >= text.Lines.Count || column < 0 || endColumn < 0
                || column > text.Lines[line].Span.Length || endColumn > text.Lines[endLine].Span.Length) return false;
            int start = text.Lines[line].Start + column;
            int end = text.Lines[endLine].Start + endColumn;
            if (end < start || SplitsSurrogate(text, start) || SplitsSurrogate(text, end)) return false;
            span = TextSpan.FromBounds(start, end);
            return true;
        }
        catch (Exception error) when (error is KeyNotFoundException or InvalidOperationException or FormatException)
        { return false; }
    }

    /// <summary>Reject half-surrogate positions rather than rounding an engine location.</summary>
    private static bool SplitsSurrogate(SourceText text, int position) => position > 0 && position < text.Length
        && char.IsHighSurrogate(text[position - 1]) && char.IsLowSurrogate(text[position]);

    /// <summary>Publish original declaration-token positions in portable UTF-16 units.</summary>
    private static object DescribeSpan(string path, SourceText text, TextSpan span)
    {
        var location = text.Lines.GetLinePositionSpan(span);
        return new { path, line = location.Start.Line, column = location.Start.Character,
            endLine = location.End.Line, endColumn = location.End.Character };
    }

    /// <summary>Describe receiver typing as lexical evidence, without claiming runtime dispatch.</summary>
    private static string? ReceiverType(SyntaxNode syntax, SemanticModel model)
    {
        if (syntax is InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax access })
            return Truncate(model.GetTypeInfo(access.Expression).Type?.ToDisplayString() ?? "unknown", 400);
        return null;
    }

    /// <summary>Bound descriptive labels independently of exact source locations.</summary>
    private static string Truncate(string value, int limit) => value.Length <= limit ? value : value[..limit];

    /// <summary>Unresolved or ambiguous symbols stay unknown, never a fabricated favorable score.</summary>
    private static object Unknown(string reason) => new { status = "unknown", reason };
}
