-- Create one Apple Notes note whose HTML body references local files
-- (mail-to-note import, mailin.py).
--   osascript notes_create.applescript <account|""> <folder|folder id|""> <body.html> <file>...
-- "" account = the Notes default account; "" folder = its default folder.
-- Prints the new note's id.
--
-- Why AppleScript and not the JXA bridge: Notes is sandboxed and can read a
-- file only after it received a reference to it in an Apple Event. `exists`
-- on the list of POSIX files delivers those references (no side effect);
-- the <img src="file://..."> tags in the body are then imported as real,
-- correctly typed attachments at their place in the text. Without it they
-- import as empty images. JXA cannot send such a reference (-1728), and a
-- data: URI imports as an untyped blob; `make new attachment` inserts the
-- attachment twice.
on run argv
	set acctName to item 1 of argv
	set folderRef to item 2 of argv
	set bodyText to read (POSIX file (item 3 of argv)) as «class utf8»
	set refs to {}
	repeat with i from 4 to count of argv
		set end of refs to POSIX file (item i of argv)
	end repeat
	tell application "Notes"
		if acctName is "" then
			set acct to default account
		else
			set acct to account acctName
		end if
		if folderRef is "" then
			set f to default folder of acct
		else if folderRef starts with "x-coredata://" then
			set f to folder id folderRef
		else
			-- same rule as the sync bridge: never guess between namesakes
			set fs to (folders of acct whose name is folderRef)
			if (count of fs) is not 1 then error "folder \"" & folderRef & "\" is not unique (" & (count of fs) & " found)"
			set f to item 1 of fs
		end if
		if refs is not {} then exists refs
		set n to make new note at f with properties {body:bodyText}
		return id of n
	end tell
end run
