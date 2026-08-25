' Starts the radar hidden (autostart at logon), appending output to data\radar.log.
' pythonw.exe is deliberately not used: it would drop the log along with the console.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
python = projectDir & "\.venv\Scripts\python.exe"
logDir = projectDir & "\data"
If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)

shell.CurrentDirectory = projectDir
' Without this the log gets Cyrillic mangled by the console codepage.
shell.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
shell.Environment("PROCESS")("PYTHONUTF8") = "1"
' Userbot mode (tg_lead_radar) sees private chats and has no polling delay.
' Without my.telegram.org credentials use tg_lead_radar.web instead.
cmd = "cmd /c """"" & python & """ -u -m tg_lead_radar >> """ & logDir & "\radar.log"" 2>&1"""
shell.Run cmd, 0, False
