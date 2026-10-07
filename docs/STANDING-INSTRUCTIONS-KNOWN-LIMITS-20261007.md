# Standing instructions: known limits (2026-10-07)

`core/standing_instructions.py` reads the owner's sentences without calling a model. These are its
limits as built, and each one is a deliberate trade-off.

1. **A bare whole-turn order can be a one-off.** "Reply in French." sent as the whole turn is saved
   as a standing language rule, because a plain rule is saved when the turn holds nothing but
   instructions. The owner may have meant only the next answer. Beside a task or a question the same
   words are not saved.
2. **Settings are a fixed table.** A new instruction replaces the old one only on a setting the table
   names: length, language, temperature, distance or weight unit, unit system, date format, time
   format, tone, closing line, tables, bullets, headings, emoji. Two opposite instructions on a
   setting outside the table both stay active until one is taken back.
3. **A rule with no trigger words needs a word about how answers look.** A whole-turn imperative is
   saved as a plain rule only when it names a presentation setting or answers and replies themselves.
   An unrelated imperative ("Write a haiku about autumn.") is treated as a task. So is a plain rule
   about something else, until it is said with "always", "from now on" or "whenever".
4. **A take-back is matched by topic words.** It removes every active instruction that shares one of
   its content words in the same owner and workspace. Two rules that share a word are both removed.
5. **A complaint needs a setting word.** "That was way too formal" is saved. "You sound like a robot"
   with no tone, length or layout word is not.
6. **Lasting facts are a few shapes, and only with a redo.** Diet, allergy, intolerance and
   kosher/halal statements in the owner's first person are saved when the same turn asks for a redo.
   Other lasting facts are left to memory capture.
7. **Two rule stores until reconciliation.** See `docs/RULES-RECONCILE-20261007.md`. While main's
   user_rules and this module both exist, "forget the rule about X" is caught by the rules lane (D2).
8. **A single bare complaint is never learned.** "Too long.", "Less formal." and "More bullet points
   please" have no subject and no order, so they are read as being about the answer just given. This
   trades some learning for safety, and it is deliberate. Say what they are about
   ("your answers are too long") or what to do instead ("Too stiff. Loosen up a bit.") to make them
   standing.
9. **An order with material, or one that produces something, is a task.** Text after a colon or a
   dash, or a quote or paste, makes the order about that text. An object that is a thing to write
   ("a short email", "a formal reply to the supplier") makes it a request. Neither is saved as a rule
   unless it uses standing wording ("from now on …").
