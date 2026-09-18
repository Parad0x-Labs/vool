' Windowless launcher: runs VOOL.cmd hidden so no console window flashes when VOOL starts.
' The shortcut points here (wscript runs a .vbs with no window of its own).
Dim shell, here
Set shell = CreateObject("WScript.Shell")
here = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
shell.Run """" & here & "VOOL.cmd""", 0, False
