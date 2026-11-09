"""Явные официальные источники дополнительных справочников и границы версий."""

MDN = {"url": "https://codeload.github.com/mdn/content/zip/refs/heads/main", "cacheKey": "javascript-mdn",
       "sourceUrl": "https://github.com/mdn/content", "license": "CC-BY-SA-2.5 / CC0 examples (upstream LICENSE)"}
DOTNET_DOCS = {"url": "https://codeload.github.com/dotnet/docs/zip/refs/heads/main", "cacheKey": "csharp-c#",
               "sourceUrl": "https://github.com/dotnet/docs", "license": "CC-BY-4.0 / MIT (upstream licenses)"}
CSHARP_SPEC = {"url": "https://codeload.github.com/dotnet/csharplang/zip/refs/heads/main", "cacheKey": "csharp-spec-source",
               "sourceUrl": "https://github.com/dotnet/csharplang", "license": "Microsoft C# specifications terms (upstream spec/LICENSE.md)"}
POSTGRES = {"url": "https://download.postgresql.org/pub/source/v18.0/postgresql-18.0-docs.tar.gz",
            "cacheKey": "postgresql-18.0", "format": "tar.gz", "version": "18.0",
            "sourceUrl": "https://www.postgresql.org/docs/18/", "license": "PostgreSQL License (upstream copyright)"}

ADDITIONAL_SOURCES = [
    {**POSTGRES, "language": "postgresql", "title": "PostgreSQL 18.0 complete manual", "includes": ["*/html/*.html"]},
    {**POSTGRES, "language": "sql", "version": "fundamentals", "title": "SQL fundamentals — PostgreSQL examples, not a universal dialect",
     "includes": ["*/html/tutorial*.html", "*/html/queries*.html", "*/html/sql-syntax.html", "*/html/syntax*.html",
                  "*/html/ddl*.html", "*/html/dml*.html", "*/html/typeconv*.html", "*/html/features.html"]},
    {"language": "lancedb", "version": "rolling", "title": "LanceDB open-source guides and SDK reference snapshot",
     "url": "https://codeload.github.com/lancedb/lancedb/zip/refs/heads/main", "cacheKey": "lancedb-source",
     "sourceUrl": "https://github.com/lancedb/lancedb", "license": "Apache-2.0 (upstream LICENSE)",
     "includes": ["*/docs/src/*.md", "*/docs/src/*.mdx", "*/docs/web/*.md", "*/docs/web/*.mdx"]},
    {**MDN, "language": "html", "version": "living-standard", "title": "MDN HTML reference and guides"},
    {**MDN, "language": "css", "version": "living-standard", "title": "MDN CSS reference and guides"},
    {"language": "git", "version": "2.51.0", "title": "Git 2.51.0 command reference and manuals",
     "url": "https://codeload.github.com/git/git/zip/refs/tags/v2.51.0", "cacheKey": "git-source",
     "sourceUrl": "https://github.com/git/git/tree/v2.51.0", "license": "GPL-2.0 (upstream COPYING)"},
    {"language": "sh", "version": "POSIX.1-2024", "title": "POSIX shell language, execution environment and sh command",
     "cacheKey": "sh-posix-2024", "format": "pages",
     "sourceUrl": "https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html",
     "license": "Copyright IEEE and The Open Group; see source copyright and terms",
     "pages": [("posix/V3_chap02.html", "https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html"),
               ("posix/V3_chap01.html", "https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap01.html"),
               ("posix/sh.html", "https://pubs.opengroup.org/onlinepubs/9799919799/utilities/sh.html")]},
    {"language": "bat", "version": "windows-cmd", "title": "Windows CMD and batch command reference",
     "url": "https://codeload.github.com/MicrosoftDocs/windowsserverdocs/zip/refs/heads/main", "cacheKey": "bat-source",
     "sourceUrl": "https://github.com/MicrosoftDocs/windowsserverdocs/tree/main/WindowsServerDocs/administration/windows-commands",
     "license": "CC-BY-4.0 / MIT (upstream licenses)"},
    {"language": "dotnet", "version": "10", "title": ".NET 10 API — official net-10.0 framework index",
     "url": "https://codeload.github.com/dotnet/dotnet-api-docs/zip/refs/heads/main", "cacheKey": "csharp-.net",
     "sourceUrl": "https://github.com/dotnet/dotnet-api-docs", "license": "CC-BY-4.0 / MIT (upstream licenses)", "framework": "net-10.0"},
    {**DOTNET_DOCS, "language": "dotnet", "version": "10", "title": ".NET 10 release, libraries and SDK guides",
     "includes": ["*/docs/core/whats-new/dotnet-10/*.md", "*/docs/core/compatibility/*/10.0/*.md"]},
]
for version in ("12", "13"):
    ADDITIONAL_SOURCES.extend([
        {**CSHARP_SPEC, "language": "csharp", "version": version, "title": "C# " + version + " feature specifications",
         "includes": ["*/proposals/csharp-" + version + ".0/*.md"]},
        {**DOTNET_DOCS, "language": "csharp", "version": version, "title": "C# " + version + " release guide",
         "includes": ["*/docs/csharp/whats-new/csharp-" + version + ".md"]},
    ])

EF_GUIDES = {"url": "https://codeload.github.com/dotnet/EntityFramework.Docs/zip/refs/heads/main",
             "cacheKey": "entityframework-guides", "sourceUrl": "https://github.com/dotnet/EntityFramework.Docs",
             "license": "CC-BY-4.0 / MIT (upstream licenses)"}
ADDITIONAL_SOURCES.extend([
    {**EF_GUIDES, "language": "efcore", "version": "multi-version", "title": "Entity Framework Core official guides",
     "includes": ["*/entity-framework/core/*.md", "*/entity-framework/efcore-and-ef6/*.md"]},
    {**EF_GUIDES, "language": "ef6", "version": "6.x", "title": "Entity Framework 6 official guides",
     "includes": ["*/entity-framework/ef6/*.md", "*/entity-framework/efcore-and-ef6/*.md"]},
    {"language": "ef6", "version": "6.5.1", "title": "Entity Framework 6.5.1 API — NuGet net45 XML",
     "url": "https://api.nuget.org/v3-flatcontainer/entityframework/6.5.1/entityframework.6.5.1.nupkg",
     "cacheKey": "ef6-api-6.5.1", "sourceUrl": "https://www.nuget.org/packages/EntityFramework/6.5.1",
     "license": "Apache-2.0 (upstream EF6 / NuGet license metadata)", "includes": ["lib/net45/*.xml"]},
])
for version in ("8", "9", "10"):
    # Пакеты EF Core 9 также таргетируют net8.0; версия EF не равна TFM пакета.
    target = "net10.0" if version == "10" else "net8.0"
    for package, kind in [("microsoft.entityframeworkcore", "core"), ("microsoft.entityframeworkcore.relational", "relational")]:
        cache_key = "efcore-api-" + version if kind == "core" else "efcore-relational-api-" + version
        package_version = version + ".0.0"
        ADDITIONAL_SOURCES.append({
            "language": "efcore", "version": package_version,
            "title": "Entity Framework Core " + package_version + " " + kind + " API — NuGet XML",
            "url": "https://api.nuget.org/v3-flatcontainer/" + package + "/" + package_version + "/" + package + "." + package_version + ".nupkg",
            "cacheKey": cache_key, "sourceUrl": "https://www.nuget.org/packages/" + package + "/" + package_version,
            "license": "MIT (upstream EF Core / NuGet license metadata)", "includes": ["lib/" + target + "/*.xml"],
        })
