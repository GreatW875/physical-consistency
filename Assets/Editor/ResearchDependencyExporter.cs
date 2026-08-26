using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class ResearchDependencyExporter
{
    private const string ScenePath = "Assets/Manipulation/G1OP.unity";

    [MenuItem("Tools/Research/Export G1OP Dependencies")]
    public static void Export()
    {
        var dependencies = AssetDatabase.GetDependencies(ScenePath, true)
            .OrderBy(path => path)
            .ToArray();
        var output = Path.Combine(
            Directory.GetCurrentDirectory(), "unity-dependency-manifest.txt"
        );
        File.WriteAllLines(output, dependencies);
        Debug.Log($"Exported {dependencies.Length} dependencies to {output}");
    }
}
