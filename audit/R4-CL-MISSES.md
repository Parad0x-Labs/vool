# Round 4 rerun with the clean loop (b96ee489): the answers the jury did not mark correct

Full app path (/api/chat), cloud model z-ai/glm-5.3-flashx, flags VOOL_CLEAN_LOCAL_LOOP=1 VOOL_CLEAN_LOOP_CLOUD=1
VOOL_CLEAN_LOOP_CHANGES=1, made-up fixture home, blind jury. Result 31/45 correct, 0 unsafe (OpenClaw 27, Hermes 27;
the round-4 one-call build fc0f0efe scored 4). Routes: admitted 42, not_about_own_files 5, red_turn 2.
The task set is now seen: improvements must be judged on NEW tasks later, not tuned to these rows.

Patterns seen:
- Not routed (not_about_own_files, 1 call, model says "I don't have that info"): R14, R19, R20, R26, R39 turn 1.
- Step limit hit after 5 tools without finishing (find misses the file / never reads it): R10, R22, R23, R24, R25.
- red_turn on money/insurance follow-up questions: R38 turn 2, R40 turn 1.
- Other: R42 move into Pictures refused (read-only media folder), R29 `largest` gives no folder total, R35 guesses from a zip.

```jsonl
{"id":"R10","task":"the list of stuff im selling, where did that go","answer":"couldn't find a dedicated list... closest is Documents/notes (1).txt, mentions garage sale Nov 16","verdict":"wrong_hedged","why":"missed Projects/garage sale/price list.txt","route":"admitted","tools":"find,find,find,list_dir,read_file","calls":6}
{"id":"R14","task":"how much was the damage deposit for the apartment","answer":"I don't have that info... nothing about an apartment or a damage deposit","verdict":"wrong_hedged","why":"$725 deposit is in the lease","route":"not_about_own_files","tools":"","calls":1}
{"id":"R19","task":"whats priya's new number","answer":"I don't have Priya's contact info... Want me to take a look?","verdict":"wrong_hedged","why":"did not search files","route":"not_about_own_files","tools":"","calls":1}
{"id":"R20","task":"when are the cat's next shots due","answer":"I don't have any record of your cat's vaccinations yet","verdict":"wrong_hedged","why":"vet invoice has September 2025 due date","route":"not_about_own_files","tools":"","calls":1}
{"id":"R22","task":"is the hydro bill the same as what came out of my account for it","answer":"found the bill and chequing export, but hit my step limit before reading their contents","verdict":"wrong_hedged","why":"right files, never read","route":"admitted","tools":"find,find,find,find,list_dir","calls":6}
{"id":"R23","task":"which cost more, the flights or the new tires","answer":"couldn't find flight costs... tires only in insurance notes","verdict":"wrong_hedged","why":"missed WestJet itinerary and tire change receipt","route":"admitted","tools":"find x5","calls":6}
{"id":"R24","task":"does the monthly car insurance payment match what the bank took out","answer":"$140.33 per notes, couldn't find any bank statements","verdict":"wrong_hedged","why":"missed Documents/Money/chequing_oct.csv","route":"admitted","tools":"find,find,read_file,find,list_dir","calls":6}
{"id":"R25","task":"if everything sells at the garage sale will it cover the christmas presents","answer":"no file with an expected amount; budget $600, $64.99 spent","verdict":"wrong_sure","why":"price list.txt has the $303 total","route":"admitted","tools":"find,find,read_file,read_file,find","calls":6}
{"id":"R26","task":"is maya's eye appt on the same day as theo's soccer? do they clash","answer":"I don't have any calendar data","verdict":"wrong_hedged","why":"eye exam Wed 4:15 pm, soccer Wed 6:00, no clash","route":"not_about_own_files","tools":"","calls":1}
{"id":"R29","task":"how much space do the songs in my music folder take up","answer":"only have the two largest songs, 7.7 MB and 4.6 MB, not a total","verdict":"wrong_hedged","why":"no 12.9 MB total","route":"admitted","tools":"largest","calls":2}
{"id":"R35","task":"show me the pics from theo's birthday party","answer":"Downloads/photos_from_aunt_rita.zip, likely the party pics","verdict":"wrong_sure","why":"no such photos exist, should say not found","route":"admitted","tools":"find,find","calls":3}
{"id":"R38","task":"whens my car insurance up for renewal / and how much am i paying for it a year right now","answer":"turn 1 right (Feb 28 2025, $1,684/year); final reply declined, retrieval returned nothing usable","verdict":"wrong_hedged","why":"final reply gave neither","route":"admitted, then red_turn","tools":"find,read_file","calls":5}
{"id":"R39","task":"when's the garage sale again / does that clash with anything the kids have","answer":"no date for the garage sale... then found kids' fall schedule","verdict":"wrong_hedged","why":"Sat Nov 16 clashes with Maya's Saturday 10:30 swim","route":"not_about_own_files, then admitted","tools":"find,read_file","calls":4}
{"id":"R40","task":"what was the biggest thing out of my bank account in october / ok not counting rent","answer":"turn 1 refused: no access to your bank account; turn 2 gave Kal Tire $963.16","verdict":"correct","why":"final reply correct","route":"red_turn, then admitted","tools":"find,find,read_file","calls":6}
{"id":"R42","task":"can u move the funny cat gif into my pictures","answer":"can't move files into Pictures, only Desktop, Downloads, Documents","verdict":"wrong_hedged","why":"did not propose the requested move","route":"admitted","tools":"find,propose_change","calls":3}
```
