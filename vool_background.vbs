Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
runtimeHome = fso.GetAbsolutePathName(scriptDir & "\..\.vool_runtime")

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = scriptDir

Set env = shell.Environment("Process")
env("PYTHONPATH") = scriptDir
env("VOOL_PROJECT_ROOT") = scriptDir
If env("VOOL_HOME") = "" Then
  env("VOOL_HOME") = runtimeHome
End If
If env("OLLAMA_API_KEY") = "" Then
  env("OLLAMA_API_KEY") = "ollama-local"
End If

q = Chr(34)
cmd = "cmd /c " & q & q & scriptDir & "\vool_background.cmd" & q & q
shell.Run cmd, 0, False
