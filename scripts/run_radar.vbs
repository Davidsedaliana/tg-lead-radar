' Starts the radar hidden (autostart at logon), appending output to data\radar.log.
' pythonw.exe is deliberately not used: it would drop the log along with the console.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
python = projectDir & "\.venv\Scripts\python.exe"
logDir = projectDir & "\data"
If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)

shell.CurrentDirectory = projectDir
cmd = "cmd /c """"" & python & """ -u -m tg_lead_radar.web >> """ & logDir & "\radar.log"" 2>&1"""
shell.Run cmd, 0, False
