' Dictation relay: start hidden at login (tray icon only).
' Works from wherever this file lives (the laptop\ folder of the repo): no hard-coded paths.
' To start at login, run install-autostart.cmd (it writes a launcher into your Startup folder).
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c uv run --gui-script relay.py", 0, False
