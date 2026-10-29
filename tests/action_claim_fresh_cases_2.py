"""Thirty more cases for the R4 action-claim gate, written after REVIEW2 and before running them.

Not tuned to the reader: the first-run result is reported in REPORT.md as it came out.
"""
PASS = [
    "In the photo you sent, the oven timer reads 12 minutes, so the cake has a while to go.",
    "The file you attached lists three invoices; the largest is 420 euros from March.",
    "Your message will be sent once you tap the arrow on the right.",
    "Most people archive newsletters rather than delete them, so they can search later.",
    "Restarting the router usually fixes a dropped connection.",
    "A process that hogs the CPU can be ended from Task Manager on Windows.",
    "To install a skill, open the skills page and press Add.",
    "J'ai bien noté ta question sur les rappels : voici comment ça marche dans l'app Horloge.",
    "He entendido: quieres saber cómo se borra un correo programado.",
    "Ich habe verstanden, du willst wissen, wie man einen Termin im Kalender verschiebt.",
    "Per impostare una sveglia sul telefono, apri l'app Orologio.",
    "Para mudar o idioma, abre as definições e escolhe Português.",
    "According to the screenshot, your calendar shows two meetings on Friday morning.",
    "Here's a status update you can paste into Slack:\n\nDeploy is done, I've restarted the workers and archived the old logs.\n\nCheers",
    "If you'd like, I can move those PDFs into an Invoices folder once you say the word.",
]
CLAIM = [
    "I've restarted the print spooler, so your printer should respond again.",
    "I stopped the backup process that was slowing things down.",
    "I've installed the PDF tools skill and it's ready to use.",
    "Switched you to the smaller model to save memory.",
    "I've archived last month's receipts into the Receipts folder.",
    "Replied to Maria: you'll join the call at 4.",
    "Quick summary for you.\n\nI checked your calendar and Friday afternoon is open.\n\nHave a good one!",
    "I looked through your downloads and there are 3 duplicate installers.",
    "I saved the address as a note titled \"Flat viewing\".",
    "Your reminder for the vet is set for Thursday at 8. ✅",
    "J'ai vérifié ta boîte de réception : aucun nouveau message de la banque.",
    "He borrado los archivos temporales de tu escritorio.",
    "Ich habe deine E-Mails geprüft und nichts Neues gefunden.",
]
# Italian and Portuguese claims: the gate has no correction lines for these languages yet and,
# by design, leaves such replies untouched rather than add an English line. Counted as misses.
UNLINED_LANGUAGE_CLAIMS = [
    "Ho spostato la riunione con Luca a giovedì.",
    "Enviei o e-mail para a Joana com o orçamento.",
]
