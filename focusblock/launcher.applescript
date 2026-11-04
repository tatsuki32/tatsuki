-- FocusBlock.app: ブロック中ならその場で解除でき、それ以外は設定画面を開く。
-- このアプリはブロックされないので、ブラウザが使えないときの解除手段になる。
property ctl : "/Library/Application Support/FocusBlock/focusctl.py"
property uiURL : "http://127.0.0.1:47800"

on run
	try
		set info to do shell script "/usr/bin/python3 " & quoted form of ctl & " status"
	on error
		display dialog "FocusBlock に接続できません。少し待ってからもう一度開いてください。" buttons {"閉じる"} default button 1 with title "FocusBlock"
		return
	end try
	if info is "idle" then
		open location uiURL
		return
	end if
	set picked to button returned of (display dialog "ブロック中です（" & info & "）" buttons {"閉じる", "設定画面を開く", "パスワードで解除"} default button 3 with title "FocusBlock")
	if picked is "設定画面を開く" then
		open location uiURL
	else if picked is "パスワードで解除" then
		unlock()
	end if
end run

on unlock()
	set msg to "解除パスワードを入力してください"
	repeat
		try
			set pw to text returned of (display dialog msg default answer "" with hidden answer buttons {"キャンセル", "解除"} default button 2 cancel button 1 with title "FocusBlock")
		on error
			return
		end try
		try
			do shell script "printf %s " & quoted form of pw & " | /usr/bin/python3 " & quoted form of ctl & " stop"
			display dialog "解除しました。" buttons {"OK"} default button 1 with title "FocusBlock"
			return
		on error errText
			set msg to errText & "。もう一度入力してください"
		end try
	end repeat
end unlock
