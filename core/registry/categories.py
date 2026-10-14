"""Fixed registry taxonomy: eleven main categories, each with its own subcategories.

Metadata stores the slugs; labels are display text for the website and Add-ons page. Signed
packages carry these slugs, so a slug is never renamed or removed: new ones are added, and
only labels change.
"""
from __future__ import annotations

TAXONOMY: dict[str, tuple[str, dict[str, str]]] = {
    "writing": ("Writing", {
        "emails-letters": "Emails & letters", "articles-blogs": "Articles & blogs",
        "social-posts": "Social posts (single posts)", "editing-proofreading": "Editing & proofreading",
        "translation": "Translation", "cv-job-applications": "CV & job applications"}),
    "productivity": ("Productivity", {
        "meetings": "Meetings", "notes-summaries": "Notes & summaries",
        "planning-tasks": "Planning & tasks", "scheduling": "Scheduling"}),
    "research-learning": ("Research & learning", {
        "web-research": "Web research", "study-tutoring": "Study & tutoring",
        "fact-checking": "Fact checking", "reading": "Reading"}),
    "coding": ("Coding", {
        "code-review": "Code review", "debugging": "Debugging", "docs": "Docs",
        "scripts-devops": "Scripts & DevOps", "data-sql": "Data & SQL"}),
    "business": ("Business", {
        "marketing": "Marketing & campaigns", "sales-outreach": "Sales & outreach",
        "customer-support": "Customer support", "hiring": "Hiring",
        "contracts-documents": "Contracts & documents"}),
    "money-crypto": ("Money & crypto", {
        "budgeting": "Budgeting", "wallet-explained": "Wallet & transactions explained",
        "market-data": "Market data (read-only)", "tax-notes": "Tax notes",
        "invoices-receipts": "Invoices & receipts"}),
    "data-analysis": ("Data & analysis", {
        "spreadsheets": "Spreadsheets", "charts": "Charts", "reports": "Reports",
        "data-cleanup": "Data cleanup"}),
    "creative": ("Creative", {
        "image-prompts": "Image prompts", "video-scripts": "Video, scripts & editing",
        "music-audio": "Music & audio", "design-ideas": "Design ideas"}),
    "personal-home": ("Personal & home", {
        "health-fitness": "Health & fitness", "food-recipes": "Food & recipes",
        "travel": "Travel", "family-home": "Family & home", "paperwork-admin": "Paperwork & admin",
        "shopping": "Shopping"}),
    "automation": ("Automation", {
        "files-folders": "Files & folders", "browser-tasks": "Browser tasks",
        "system-helpers": "System helpers", "integrations": "Connected apps (email, calendar, notes)"}),
    "privacy-security": ("Privacy & security", {
        "privacy-checkup": "Privacy checkup", "scams-phishing": "Scams & phishing",
        "safe-sharing": "Safe sharing", "account-safety": "Account safety"}),
}


def is_listed(category: object, subcategory: object) -> bool:
    entry = TAXONOMY.get(category) if isinstance(category, str) else None
    return entry is not None and isinstance(subcategory, str) and subcategory in entry[1]


def public_taxonomy() -> list[dict]:
    return [{"id": key, "label": label, "subcategories": [{"id": sub, "label": text} for sub, text in subs.items()]}
            for key, (label, subs) in TAXONOMY.items()]
