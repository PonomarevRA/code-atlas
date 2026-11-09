using System;
using System.IO;
using System.Reflection;
using System.Runtime.Loader;

/// <summary>Load only the user's installed SDK Roslyn assemblies before JIT-compiling the worker.</summary>
internal static class Bootstrap
{
    /// <summary>Reject arbitrary resolver locations; this bridge never loads an analyzed project's code.</summary>
    public static int Main(string[] args)
    {
        if (args.Length is < 2 or > 3 || args[0] != "--roslyn" || !Path.IsPathFullyQualified(args[1]))
            return 2;
        string directory = args[1];
        AssemblyLoadContext.Default.Resolving += (_, name) => Resolve(directory, name);
        try { return Worker.Run(args.Length == 3 && args[2] == "--probe"); }
        catch (Exception error)
        {
            Console.Error.WriteLine("roslyn-worker-failed:" + error.GetType().Name);
            return 3;
        }
    }

    /// <summary>System dependencies come from .NET; only two explicitly named compiler DLLs use this resolver.</summary>
    private static Assembly? Resolve(string directory, AssemblyName name)
    {
        if (name.Name is not ("Microsoft.CodeAnalysis" or "Microsoft.CodeAnalysis.CSharp")) return null;
        string path = Path.Combine(directory, name.Name + ".dll");
        return File.Exists(path) ? AssemblyLoadContext.Default.LoadFromAssemblyPath(path) : null;
    }
}
