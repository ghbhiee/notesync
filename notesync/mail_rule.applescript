-- Install or remove the Mail rule that wakes NoteSync when mail for the
-- import address arrives (mail-to-note import, real-time trigger).
--   osascript mail_rule.applescript install <rule name> <compiled script> <address>...
--   osascript mail_rule.applescript remove <rule name>
-- The rule only runs the script (which touches the trigger file); it never
-- moves, marks or stops other rules, and it goes first so that no earlier
-- rule can keep it from running.
on run argv
	set cmd to item 1 of argv
	set ruleName to item 2 of argv
	if cmd is "install" then set scriptFile to POSIX file (item 3 of argv)
	tell application "Mail"
		delete (every rule whose name is ruleName) -- idempotent: replace an earlier copy
		if cmd is "remove" then return "removed"
		set r to make new rule at beginning of rules with properties {name:ruleName, enabled:false, all conditions must be met:false, stop evaluating rules:false, should move message:false, should copy message:false, mark read:false, mark flagged:false, delete message:false}
		delete every rule condition of r -- a new rule comes with a default one
		-- recipients only: a `header key` condition (iCloud's Original-recipient,
		-- for Bcc) is not scriptable -- created in one go it silently becomes
		-- From, and setting its type afterwards crashed Mail. Bcc'd mail is
		-- left to the poll, whose scan does check that header.
		repeat with i from 4 to count of argv
			make new rule condition at end of rule conditions of r with properties {rule type:any recipient, qualifier:does contain value, expression:(item i of argv)}
		end repeat
		set run script of r to scriptFile
		set enabled of r to true
		return "installed"
	end tell
end run
