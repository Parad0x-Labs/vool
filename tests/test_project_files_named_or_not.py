"""A question reads a project file only when it names that file, and secrets never reach the model."""
import json

import pytest

from core.local_inference_autopilot import _explicit_heavy_requested
from tests.test_workspace_subject_evidence import enter_turn
from tests.test_workspace_subject_evidence import isolated_home as isolated_home

SEED_WORDS = ["orbit", "velvet", "canyon", "ladder", "pepper", "island",
              "frost", "humble", "rocket", "maple", "quiz", "anchor"]


def _turn_reads(workspace, files, question):
    workspace.mkdir()
    for name, text in files.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    _, context = enter_turn(question, workspace)
    observations = context.get("runtime_tool_observations", [])
    read = sorted(str(item.get("path") or "") for item in observations if item.get("intent") == "workspace.read_file")
    from core.prompt_normalizer import _runtime_tool_observation_message

    message = _runtime_tool_observation_message(context)
    sent = json.dumps(observations) + (message.content if message is not None else "")
    return read, sent


@pytest.mark.parametrize("files,question,secret", [
    ({"bitwarden_export.json": '{"items":[{"login":{"password":"Hunter2-xq81"}}]}\n'},
     "How does a bitwarden export work in general?", "Hunter2-xq81"),
    ({"passport_details.txt": "Passport number X9922817\n"},
     "What details does a passport have?", "X9922817"),
    ({"backup_codes.txt": "4471-2209\n"}, "What do backup codes do?", "4471-2209"),
    ({"bank_login.md": "user amy pass Zz-81kk\n"}, "How does a bank login work?", "Zz-81kk"),
    ({"passport.txt": "Passport number X9922817\n"}, "What is the passport fee in the UK?", "X9922817"),
    ({"salary.csv": "year,gross\n2025,98410\n"}, "What is the salary of a nurse in Texas?", "98410"),
    ({"name.txt": "Amy Hartwell\n"}, "What is the meaning of my name?", "Hartwell"),
    ({"medical_history.txt": "Takes Biktarvy daily\n"}, "What is the medical history of aspirin?", "Biktarvy"),
    ({"address.txt": "12 Larch Close\n"}, "What is the address of the White House?", "Larch"),
    ({"taxes/2025-return.txt": "SSN on file\n"}, "When is the 2025 return deadline?", "SSN on file"),
    ({"therapy-notes.md": "Talked about the panic attacks\n"}, "Are the therapy notes confidential by law?", "panic"),
])
def test_a_general_question_reads_no_personal_file(tmp_path, files, question, secret):
    read, sent = _turn_reads(tmp_path / "workspace", files, question)
    assert read == []
    assert secret not in sent


@pytest.mark.parametrize("layout", [
    "\n".join(f"{i}. {w}" for i, w in enumerate(SEED_WORDS, 1)),
    "\n".join(SEED_WORDS),
    ", ".join(SEED_WORDS),
    " ".join(w.capitalize() for w in SEED_WORDS),
    " ".join(SEED_WORDS[:6]) + "\n" + " ".join(SEED_WORDS[6:]),
    "\n".join(f"{i + 1}. {SEED_WORDS[i]}    {i + 7}. {SEED_WORDS[i + 6]}" for i in range(6)),
    "\n".join(f"{i + 1},{SEED_WORDS[i]},{i + 7},{SEED_WORDS[i + 6]}" for i in range(6)),
    "| # | word | # | word |\n|---|---|---|---|\n"
    + "\n".join(f"| {i + 1} | {SEED_WORDS[i]} | {i + 7} | {SEED_WORDS[i + 6]} |" for i in range(6)),
    "Recovery phrase - " + " ".join(SEED_WORDS),
    "-".join(SEED_WORDS),
    " ".join(SEED_WORDS) + ".",
])
def test_a_named_file_holding_a_word_phrase_is_withheld_in_any_layout(tmp_path, layout):
    read, sent = _turn_reads(tmp_path / "workspace", {"ledger_backup.txt": layout + "\n"},
                             "What does my ledger backup say?")
    assert read == ["ledger_backup.txt"]
    assert "canyon" not in sent.lower()


@pytest.mark.parametrize("content,secret", [
    ("site,username,password\ngmail,amy,Hunter2xq81\n", "Hunter2xq81"),
    ("machine api.example.com login amy password Hunter2xq81\n", "Hunter2xq81"),
    ("Passwort: Hx81kkzq\n", "Hx81kkzq"),
    ("mot de passe : Hx81kkzq\n", "Hx81kkzq"),
    ("pw=Hx81kkzq\n", "Hx81kkzq"),
    ("ábaco abdomen abeja abierto abogado abono aborto abrazo abrir abuelo abuso acabar\n", "abogado"),
    ("Name Amy, SSN 123-45-6789\n", "123-45-6789"),
])
def test_a_named_file_holding_a_secret_is_withheld(tmp_path, content, secret):
    read, sent = _turn_reads(tmp_path / "workspace", {"home_admin_notes.txt": content},
                             "What do my home admin notes say?")
    assert read == ["home_admin_notes.txt"]
    assert secret not in sent


@pytest.mark.parametrize("name,question", [
    ("server_secretkey.txt", "What does my server secretkey say?"),
    ("github_accesstoken.json", "What is in my github accesstoken?"),
    ("card_pins.txt", "What are my card pins?"),
    ("work_contraseñas.txt", "What do my work contraseñas say?"),
    ("alte_Passwörter.txt", "What do my alte Passwörter say?"),
    ("work_netrc", "What does my work netrc say?"),
])
def test_a_secret_file_name_is_never_read_even_when_named(tmp_path, name, question):
    read, sent = _turn_reads(tmp_path / "workspace", {name: "opaque-value-Q81\n"}, question)
    assert read == []
    assert "opaque-value-Q81" not in sent


@pytest.mark.parametrize("files,question,expected", [
    ({"dentist_notes.md": "Dentist on Friday 10:30.\n"}, "What do my dentist notes say?", "dentist_notes.md"),
    ({"invoices/march_invoice.txt": "Total due 412.80\n"}, "What is the total on the march invoice?",
     "invoices/march_invoice.txt"),
    ({"docs/auth_flow.md": "Tokens expire after 15 minutes.\n"}, "What does the auth flow doc say about tokens?", "docs/auth_flow.md"),
    ({"budget2025.csv": "rent,1450\n"}, "What is in my budget 2025?", "budget2025.csv"),
    ({"therapy_journal.md": "Slept better this week.\n"}, "What does my therapy journal say?", "therapy_journal.md"),
    ({"private_beta_plan.md": "Invite 40 testers.\n"}, "What does the private beta plan say?", "private_beta_plan.md"),
])
def test_a_question_that_names_a_file_still_reads_it(tmp_path, files, question, expected):
    read, _ = _turn_reads(tmp_path / "workspace", files, question)
    assert read == [expected]


@pytest.mark.parametrize("files,question", [
    ({"empire_sales_q3.csv": "region,total\nnorth,55120.75\n"}, "When did the Roman empire fall?"),
    ({"capital_gains_2025.csv": "asset,gain\nACME,8812.40\n"}, "What is the capital of France?"),
    ({"notes.md": "Dentist on Friday 10:30.\n"}, "What do the notes of a lecture usually cover?"),
])
def test_one_shared_word_does_not_name_a_file(tmp_path, files, question):
    read, _ = _turn_reads(tmp_path / "workspace", files, question)
    assert read == []


def test_a_one_word_file_is_read_when_its_name_is_written_out(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.md").write_text("Dentist on Friday 10:30.\n")
    result, _ = enter_turn("What does notes.md say about the dentist?", workspace)
    assert "Dentist on Friday" in result["response"]


@pytest.mark.parametrize("text", [
    "the order code is AB70B", "ship it to apt45b", "flat24b", "ref TX88b", "my seat is row32b",
    "SKU ZX400B is out of stock", "revert commit fa9e32b", "deliver to Apt-45b", "my seat is row-32b",
    "order AB-70B", "SKU ZX-400B", "Gate-30B", "suite_30b", "Lane45B", "Tower30b", "Wohnung24b",
])
def test_codes_with_a_size_are_not_model_requests(text):
    assert _explicit_heavy_requested(user_text=text, source_context={}) is False


@pytest.mark.parametrize("text", [
    "run gemma27b on this", "go with Llama3.3_70B", "please load mistral_123b", "llama-3.1-405b",
    "qwen2.5-coder-32b", "deepseek-r1:671b", "mixtral-8x22b", "gpt-oss-120b", "llama70b",
    "run foo-70b", "Can my box run 70b?",
])
def test_model_names_with_a_size_are_heavy_requests(text):
    assert _explicit_heavy_requested(user_text=text, source_context={}) is True
