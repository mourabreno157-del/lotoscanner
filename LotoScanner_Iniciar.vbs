Set sh = CreateObject("WScript.Shell")
base = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
sh.Run "pythonw.exe server.py", 0, False
WScript.Sleep 1800
sh.Run "http://127.0.0.1:8000", 1, False
