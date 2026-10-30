"""The price guard must not wipe answers about the user's OWN figures (2026-10-30).

Measured on the org-credit job 3 fresh run (VOOL main 411efdb2, 100 memory questions,
haiku-5-5 reader): five answers about figures the user themselves stated in earlier chats
(salary, raise, budget) were replaced by the unverified-live-value notice, or had it
inserted mid-answer, because `unobserved_live_value_claims` treated every currency figure
bound to a currentness anchor as an unobserved live reading. A figure stated by the user in
the evidence the turn carried is an observed value, not a live claim.

Fixtures are the real question, memory packet and (where it survived) delivered answer from
the five measured RESULT.json files, copied verbatim. For the three whole-answer cases the
guard destroyed the draft (the delivered text IS the notice), so the draft is reconstructed
to state exactly a figure the packet carries; for the two mid-answer cases the convicted
sentence was withdrawn and is reconstructed the same way.
"""

import pytest

from core.model_output_guard import (
    replace_unobserved_live_claims,
    unobserved_live_value_claims,
    unverified_live_value_notice,
)

NOTICE_OPENER = "I didn't run any live lookup"

QUESTION_KNOWLEDGE_UPDATE = "Answer using the imported prior conversations. You may derive only answers supported by those records. If the records do not support an answer, say you do not know. Give a concise final answer. What's my current salary?"

PACKET_KNOWLEDGE_UPDATE = 'Evidence receipts (typed facts read from the records above and below; each carries the day the record was stated and, when its own words date the event, the event day; a replaced value is marked):\n- [2024-11-18] user said: "I have an interview on November 25, and I want to make sure my resume passes any applicant tracking system, especially since I\'ve updated it to comply with the latest ATS parser versions, and I\'m also looking forward to my November 18 time anchor, so can you help me review my resume and provide tips on how to prepare for the interview, given that I\'ve already completed my certification in Advanced"  <count: 20 projects>  (event day 2024-11-18, explicit)\n- [2024-09-10] user said: "I just got a $12,000 raise in August 2024, which Joshua recommended I add to my resume, but I\'m not sure how to negotiate salary outcomes effectively, can you give me some tips? ->-> 4,4"  <amount: $12,000>  (event day 2024-09-10, recent)\n- [2024-03-15] user said: "I\'m kinda worried about balancing work and personal life, like Joshua requested I limit work calls after 7 PM, and I agreed to reduce calls by 50% starting March 18, 2024, so how can I make sure I\'m respecting that boundary without affecting my career? ->-> 1,17"  <clock: 7 PM>  (event day 2024-03-18, explicit)\n- [2024-05-02] user said: "I\'m kinda stuck on how to highlight my achievements, like when I increased viewership by 40% on April 15, 2024, after Dawn suggested adding quantified achievements - can you help me make it sound more impressive? ->-> 2,5"  (event day 2024-04-15, explicit)\n- [2024-05-02] user said: "I used Textio to identify 15 new action verbs to replace passive language in my resume by April 30, 2024, can you help me make sure I\'m using them correctly to pass any Applicant Tracking System? ->-> 2,8"  <count: 15 new action>  (event day 2024-04-30, explicit)\n- [2024-07-07] user said: "I\'m kinda stressed about my new role as a digital media executive producer at StreamWave starting August 1, 2024, and I\'m wondering if my $98,000 salary is competitive, considering I\'ve been preparing for this transition since May 2, 2024 ->-> 3,1"  <count: 000 salary>  (event day 2024-05-02, explicit)\n- [2024-07-07] user said: "I\'ve got my first performance review scheduled for October 15, 2024, and I\'m aiming for a 10% salary increase, do you have any tips on how to negotiate that? ->-> 3,6"  (event day 2024-10-15, explicit)\n- [2024-07-07] user said: "I just achieved something big, completing those 3 online courses on digital media leadership with a 95% average score by July 6, 2024, and I\'m wondering how I can best highlight this achievement in my resume to make it pass any applicant tracking system - any tips? ->-> 3,18"  <count: 3 online>  (event day 2024-07-06, explicit)\n- [2024-07-07] user said: "I just added a project on eco-friendly filming practices to my portfolio, which was completed on June 28, 2024, and I\'m wondering if that\'s enough to demonstrate my commitment to environmental sustainability, or if I should be doing more to make it stand out? ->-> 3,19"  (event day 2024-06-28, explicit)\n- [2024-09-10] user said: "I just completed my certification in Advanced Digital Media Production on September 5, 2024, and I\'m wondering how I can best highlight this new skill on my resume to make it pass any Applicant Tracking System, considering my goal is to have a portfolio-ready resume by September 10, 2024? ->-> 4,2"  (event day 2024-09-10, explicit)\n- [2024-09-10] user said: "I\'m feeling pretty confident about my resume now that I\'ve got this certification, but I want to make sure I\'m using the right keywords and formatting to get past those ATS systems, can you help me with that, given that I\'ve already completed the certification and I\'m looking to finalize my resume by September 10, 2024? ->-> 4,1"  (event day 2024-09-10, explicit)\n- [2024-09-10] user said: "I attended the Caribbean Media Innovation Summit on September 9, 2024, and gained insights on emerging ATS trends, how can I effectively incorporate these insights into my resume to make it more attractive to recruiters? ->-> 4,12"  (event day 2024-09-09, explicit)\n- [2024-09-10] user said: "I\'m really excited about my promotion and I want to make sure my LinkedIn profile reflects this new role, especially since I\'ve increased my followers by 150 since July 1, 2024 - can you help me craft a post to announce it? ->-> 4,18"  <count: 150 since>  (event day 2024-07-01, explicit)\n- [2024-11-18] user said: "I\'m planning to meet Alexis at The Harbour View Restaurant on November 22 to discuss UK market strategies, and I want to make sure my resume is tailored for the UK job market, so can you help me understand how to improve it? ->-> 5,17"  (event day 2024-11-22, explicit)\n- This question needs: latest_unreplaced_value (the latest value in the slot, with its chain). Receipts found: complete. Coverage: partial, 19 of 40 matching records shown; do not conclude that no later change exists.'

DRAFT_KNOWLEDGE_UPDATE = 'Your current salary is $98,000 a year at StreamWave.'

QUESTION_MULTI_SESSION = 'Answer using the imported prior conversations. You may derive only answers supported by those records. If the records do not support an answer, say you do not know. Give a concise final answer. How much more do I earn now than the salary I was offered when I joined StreamWave?'

PACKET_MULTI_SESSION = 'Evidence receipts (typed facts read from the records above and below; each carries the day the record was stated and, when its own words date the event, the event day; a replaced value is marked):\n- [2024-11-18] user said: "I have an interview on November 25, and I want to make sure my resume passes any applicant tracking system, especially since I\'ve updated it to comply with the latest ATS parser versions, and I\'m also looking forward to my November 18 time anchor, so can you help me review my resume and provide tips on how to prepare for the interview, given that I\'ve already completed my certification in Advanced"  <count: 20 projects>  (event day 2024-11-18, explicit)\n- [2024-05-02] user said: "I feel sorta conflicted about declining a $75,000 job offer, but I\'m hoping it\'ll pay off in the long run, you know, since I\'m targeting roles at places like Netflix and Hulu, and I\'m trying to stay focused on my goal of getting a role that pays $95,000 or more ->-> 2,10"  <amount: $75,000>\n- [2024-11-18] user said: "I\'m preparing to negotiate a £90,000 salary offer with a UK employer and aiming for a 10% increase based on market research, but what are some key points I should focus on during the negotiation to achieve my desired salary? ->-> 5,22"  <amount: £90,000>  [replaces "$110,000," stated 2024-09-10]\n- [2024-07-07] user said: "I\'m kinda stressed about my new role as a digital media executive producer at StreamWave starting August 1, 2024, and I\'m wondering if my $98,000 salary is competitive, considering I\'ve been preparing for this transition since May 2, 2024 ->-> 3,1"  <count: 000 salary>  (event day 2024-05-02, explicit)\n- [2024-09-10] user said: "I just got a $12,000 raise in August 2024, which Joshua recommended I add to my resume, but I\'m not sure how to negotiate salary outcomes effectively, can you give me some tips? ->-> 4,4"  <amount: $12,000>  (event day 2024-09-10, recent)\n- [2024-05-02] user said: "I\'m kinda stuck on how to highlight my achievements, like when I increased viewership by 40% on April 15, 2024, after Dawn suggested adding quantified achievements - can you help me make it sound more impressive? ->-> 2,5"  (event day 2024-04-15, explicit)\n- [2024-05-02] user said: "I\'m kinda worried that my portfolio website redesign on Squarespace, which I finished on May 1, 2024, might not be enough to get me the $95,000+ roles in digital media I\'m aiming for, especially since I declined that $75,000 job offer from a local station ->-> 2,9"  <amount: $95,000>  (event day 2024-05-01, explicit)\n- [2024-07-07] user said: "I updated my LinkedIn headline to “Executive Producer | Digital Storytelling | Caribbean Media Innovator” on July 5, 2024, will this help me stand out to potential employers and get more profile views ->-> 3,3"  (event day 2024-07-05, explicit)\n- [2024-07-07] user said: "I\'ve got my first performance review scheduled for October 15, 2024, and I\'m aiming for a 10% salary increase, do you have any tips on how to negotiate that? ->-> 3,6"  (event day 2024-10-15, explicit)\n- [2024-07-07] user said: "I just added a project on eco-friendly filming practices to my portfolio, which was completed on June 28, 2024, and I\'m wondering if that\'s enough to demonstrate my commitment to environmental sustainability, or if I should be doing more to make it stand out? ->-> 3,19"  (event day 2024-06-28, explicit)\n- [2024-09-10] user said: "I attended the Caribbean Media Innovation Summit on September 9, 2024, and gained insights on emerging ATS trends, how can I effectively incorporate these insights into my resume to make it more attractive to recruiters? ->-> 4,12"  (event day 2024-09-09, explicit)\n- [2024-09-10] user said: "I recently got promoted to Senior Executive Producer on September 1, 2024, with a 12% salary increase to $110,000, and I\'m wondering how to best showcase this achievement on my resume to make it pass any Applicant Tracking System ->-> 4,6"  <amount: $110,000,>  (event day 2024-09-01, explicit)  [replaces "$98,000" stated 2024-07-07]  [REPLACED later by "£90,000" stated 2024-11-18; not current]\n- [2024-03-15] user said: "I\'ve set a goal to increase my interview callbacks by 30% within 3 months after I update my resume, do you think that\'s achievable and can you offer some advice on how to make it happen? ->-> 1,10"  <duration: 3 months>\n- [2024-03-15] user said: "I\'m kinda worried that my digital portfolio on Behance, which has 12 projects from 2010 to 2023, might not be enough to get me a $10,000 salary increase by July 2024, can you help me figure out what\'s missing? ->-> 1,13"  <count: 12 projects>\n- This question needs: typed_values>=2 (every record with a value of the asked kind). Receipts found: complete. Coverage: partial, 27 of 36 matching records shown; do not total or average as if every record were here.'

DRAFT_MULTI_SESSION = 'You now earn £90,000 a year, and you joined StreamWave on a $98,000 salary.'

QUESTION_SUMMARIZATION_14 = 'Answer using the imported prior conversations. You may derive only answers supported by those records. If the records do not support an answer, say you do not know. Give a concise final answer. Can you summarize how my streaming subscriptions and home viewing setup decisions evolved over these past months?'

PACKET_SUMMARIZATION_14 = 'Evidence receipts (typed facts read from the records above and below; each carries the day the record was stated and, when its own words date the event, the event day; a replaced value is marked):\n- [2024-05-15] user said: "I just upgraded my blackout curtains to thicker fabric on May 14 to improve daytime viewing quality in my home theater setup, do you have any recommendations for the best streaming movies to test out this new setup? ->-> 5,17"  (event day 2024-05-14, explicit)\n- [2024-03-12] user said: "I\'ve got a deadline to finalize my watchlist by March 28, 2024, and I\'m hoping you can help me find some good streaming movies for a family weekend ->-> 1,21"  (event day 2024-03-28, explicit)\n- [2024-05-03] user said: "I\'m planning a movie marathon for May 11-12 and I need help finding some family-friendly films, considering Amy and Kyle will be joining us and Amy has an evening church service, so we\'ll start at 2 PM ->-> 4,3"  <clock: 2 PM>  (event day 2024-05-11, explicit)  [replaces "7:45 PM" stated 2024-05-03]\n- [2024-05-15] user said: "I need advice on how to manage our movie marathon schedule for May 11-12, especially since Amy requested an earlier start at 2 PM due to an evening church service, and we also have to consider Michelle\'s friend Lily joining us, while keeping in mind our goal to include one documentary like "March of the Penguins" for educational content, and ensuring we have enough snacks with a budget of $85, inc"  <amount: $85,>  (event day 2024-05-11, explicit)  [replaces "$30" stated 2024-05-03]\n- [2024-05-15] user said: "How does upgrading the internet to 200 Mbps for $20/month, which improved streaming quality and reduced buffering by 75%, impact our movie nights, especially for a big group like we had on May 12? ->-> 5,5"  <amount: $20>  (event day 2024-05-12, explicit)\n- [2024-05-15] user said: "I\'ve never upgraded my internet speed or streaming quality, but I\'m thinking of hosting a virtual watch party with Mason and Michael on May 12 at 7 PM using Teleparty app, so should I prioritize upgrading my internet plan from 100 Mbps to 200 Mbps for $20/month to improve streaming quality? ->-> 5,24"  <amount: $20>  (event day 2024-05-12, explicit)\n- [2024-03-12] user said: "I\'m kinda worried about my parents, Amy and Kyle, who are 63 and 77, living 15 miles away in West Janethaven, and I want to make sure they\'re doing okay, can you help me find some nice streaming movies for our family weekend? ->-> 1,4"  <measure: 15 miles>\n- [2024-04-08] assistant said: "Given that the movie is rated PG-13 and runs for about 3 hours, it might not be the best fit for a late-night viewing when the kids need to go to bed at 8 PM."  <duration: 3 hours>\n- [2024-05-03] assistant said: "However, if your current plan performs well and you rarely encounter issues, you might want to hold off on the upgrade and monitor your internet usage over the next few months."  <duration: few months>\n- [2024-05-03] assistant said: "- Before the actual watch party, it\'s a good idea to test the setup with Mason and Michael a few days in advance to ensure everything works smoothly."  <duration: a few days>\n- [2024-05-15] assistant said: "- **Late Afternoon**: 3 hours for final preparations (preparing snacks, setting up the viewing area, etc.)"  <duration: 3 hours>\n- [2024-04-08] assistant said: "- **Duration:** Approximately 3 hours"  <duration: 3 hours>\n- [2024-03-12] assistant said: "- **Convenience:** You can rent the movie immediately and watch it for a limited time (usually 48 hours)."  <duration: 48 hours>\n- This question needs: record (the record that states it). Receipts found: complete.'

DRAFT_SUMMARIZATION_14 = 'You now budget $85 for movie-night snacks and pay $20 a month for the 200 Mbps internet upgrade.'

QUESTION_SUMMARIZATION_6 = 'Answer using the imported prior conversations. You may derive only answers supported by those records. If the records do not support an answer, say you do not know. Give a concise final answer. Can you give me an overview of how my compensation and salary goals have changed over time?'

PACKET_SUMMARIZATION_6 = 'Evidence receipts (typed facts read from the records above and below; each carries the day the record was stated and, when its own words date the event, the event day; a replaced value is marked):\n- [2024-09-10] user said: "I just got a $12,000 raise in August 2024, which Joshua recommended I add to my resume, but I\'m not sure how to negotiate salary outcomes effectively, can you give me some tips? ->-> 4,4"  <amount: $12,000>  (event day 2024-09-10, recent)\n- [2024-11-18] user said: "I have an interview on November 25, and I want to make sure my resume passes any applicant tracking system, especially since I\'ve updated it to comply with the latest ATS parser versions, and I\'m also looking forward to my November 18 time anchor, so can you help me review my resume and provide tips on how to prepare for the interview, given that I\'ve already completed my certification in Advanced"  <count: 20 projects>  (event day 2024-11-18, explicit)\n- [2024-03-15] user said: "I want to improve my resume to boost confidence applying for executive producer roles by June 1, 2024, but I\'m not sure where to begin, can you give me some tips? ->-> 1,7"  (event day 2024-06-01, explicit)\n- [2024-05-02] user said: "I feel like I\'ve made significant progress since May 2, 2024, and I\'m wondering if my decision to prioritize resume update over attending the March 20 networking event, saving 6 hours weekly, was the right call to achieve my goal of increasing interview callbacks by 30% within 3 months after resume update? ->-> 2,1"  <duration: 6 hours>  (event day 2024-03-20, explicit)\n- [2024-05-02] user said: "I\'m kinda stuck on how to highlight my achievements, like when I increased viewership by 40% on April 15, 2024, after Dawn suggested adding quantified achievements - can you help me make it sound more impressive? ->-> 2,5"  (event day 2024-04-15, explicit)\n- [2024-05-02] user said: "I just attended a workshop on international resume standards on May 3, and I\'m wondering how I can adapt my resume for the UK and Canadian markets, can you give me some tips? ->-> 2,15"  (event day 2023-05-03, explicit)\n- [2024-07-07] user said: "I\'m kinda stressed about my new role as a digital media executive producer at StreamWave starting August 1, 2024, and I\'m wondering if my $98,000 salary is competitive, considering I\'ve been preparing for this transition since May 2, 2024 ->-> 3,1"  <count: 000 salary>  (event day 2024-05-02, explicit)\n- [2024-07-07] user said: "I updated my LinkedIn headline to “Executive Producer | Digital Storytelling | Caribbean Media Innovator” on July 5, 2024, will this help me stand out to potential employers and get more profile views ->-> 3,3"  (event day 2024-07-05, explicit)\n- [2024-07-07] user said: "I\'ve got my first performance review scheduled for October 15, 2024, and I\'m aiming for a 10% salary increase, do you have any tips on how to negotiate that? ->-> 3,6"  (event day 2024-10-15, explicit)\n- [2024-07-07] user said: "I just added a project on eco-friendly filming practices to my portfolio, which was completed on June 28, 2024, and I\'m wondering if that\'s enough to demonstrate my commitment to environmental sustainability, or if I should be doing more to make it stand out? ->-> 3,19"  (event day 2024-06-28, explicit)\n- [2024-09-10] user said: "I just completed my certification in Advanced Digital Media Production on September 5, 2024, and I\'m wondering how I can best highlight this new skill on my resume to make it pass any Applicant Tracking System, considering my goal is to have a portfolio-ready resume by September 10, 2024? ->-> 4,2"  (event day 2024-09-10, explicit)\n- [2024-09-10] user said: "I attended the Caribbean Media Innovation Summit on September 9, 2024, and gained insights on emerging ATS trends, how can I effectively incorporate these insights into my resume to make it more attractive to recruiters? ->-> 4,12"  (event day 2024-09-09, explicit)\n- [2024-09-10] user said: "I\'m looking forward to celebrating my promotion with my close friend Linda at The Blue Lagoon on September 12, and I was wondering if you could give me some tips on how to politely decline if she asks me to pick up the check, considering I\'m 65 and she\'s also around my age? ->-> 4,17"  (event day 2023-09-12, explicit)\n- [2024-11-18] user said: "I\'ve enrolled in a November 10 course on UK employment law and resume compliance, and I\'ve already completed 50% of it by November 18, but I\'m not sure how to apply what I\'ve learned to my resume, can you give me some guidance? ->-> 5,12"  <count: 10 course>  (event day 2024-11-10, explicit)\n- This question needs: record (the record that states it). Receipts found: complete.'

DRAFT_SUMMARIZATION_6 = '- **Mar 2024:** aiming for a $10,000 salary increase. - **May 2024:** declined a $75,000 offer, targeting roles paying $95,000 or more. - **Jul 2024:** starting at StreamWave at $98,000, with a goal of a 10% raise at the October 15 review. - **Aug 2024:** received a $12,000 raise. - **Nov 18, 2024 (latest):** promoted to Senior Executive Producer, a 12% increase to $110,000. The UK offer was GBP 90,000, and you aimed for about a 10% increase from that. That is the full arc of how your compensation and salary goals moved over these months. Right now your StreamWave salary stands at $98,000.'

QUESTION_EVENT_ORDERING = 'Answer using the imported prior conversations. You may derive only answers supported by those records. If the records do not support an answer, say you do not know. Give a concise final answer. Can you list, in order, the different money and spending decisions I brought up throughout our conversations? Mention ONLY and ONLY five items.'

PACKET_EVENT_ORDERING = 'Evidence receipts (typed facts read from the records above and below; each carries the day the record was stated and, when its own words date the event, the event day; a replaced value is marked):\n- [2024-07-10] user said: "I\'ve started meal prepping on Sundays to save time and money, reducing my weekday cooking time by 1 hour and saving $50 weekly on takeout, but I\'m wondering if there are other ways to further reduce my cooking time and save more money on food? ->-> 3,12"  <amount: $50>\n- [2024-11-20] user said: "I\'m trying to manage my budget for social media ads, and I approved $3,500 for Facebook and Instagram, but I rejected a $5,000 influencer campaign - was that a good decision? ->-> 5,5"  <amount: $3,500>\n- [2024-03-15] user said: "I\'m kinda worried about meeting my deadline for the pilot episode by June 30, 2024, with a budget cap of $120,000, can you help me figure out a plan to stay on track? ->-> 1,7"  <amount: $120,000,>  (event day 2024-06-30, explicit)\n- [2024-03-15] user said: "I\'m kinda stressed about declining that last-minute 3 PM meeting on March 14, but I really needed to protect my 9 AM writing block, you know, to save 2 hours of focused work ->-> 1,15"  <duration: 2 hours>  (event day 2024-03-14, explicit)\n- [2024-05-02] user said: "I had a call with Heather, the casting director, on March 28, and she recommended early auditions, so I shifted my focus from scriptwriting to casting, was that the right decision ->-> 2,4"  (event day 2024-03-28, explicit)\n- [2024-05-02] user said: "I just celebrated finishing casting by April 20 with a dinner at The Coral Reef, which cost $120 for four guests, and I\'m wondering if I should set aside a similar budget for other milestones, like maybe a party for when I complete the pilot episode ->-> 2,7"  <amount: $120>  (event day 2024-04-20, explicit)\n- [2024-07-10] user said: "I\'ve added 1-hour buffer slots daily for unexpected tasks starting July 1, which has reduced my deadline stress by 35%, but I\'m not sure if this is the best way to overhaul my schedule, can you help me decide if this is a good decision ->-> 3,4"  (event day 2024-07-01, explicit)\n- [2024-11-20] user said: "I\'m trying to manage my time better, especially with editing deadlines looming, like when I declined a November 12 networking event to save 5 hours and meet my deadlines ->-> 5,15"  <duration: 5 hours>  (event day 2024-11-12, explicit)\n- [2024-05-02] user said: "I\'m kinda worried about staying within my budget, I allocated $5,000 for post-production software licenses like Adobe Premiere Pro and DaVinci Resolve, which I purchased on April 25, can you help me track my expenses? ->-> 2,15"  <amount: $5,000>  (event day 2024-04-25, explicit)  [REPLACED later by "$6,200" stated 2024-05-02; not current]\n- [2024-03-15] user said: "I\'m considering switching from daily to weekly batching of emails to save 3 hours weekly, but I\'m not sure if it\'ll affect my productivity, what do you think? ->-> 1,13"  <duration: 3 hours>\n- [2024-03-15] user said: "I\'m debating if I made the right call choosing to train Michele internally instead of hiring a $1,500 freelance editor, since it saves me $900, but I\'m not sure if it\'s the best use of my time ->-> 1,16"  <amount: $1,500>\n- [2024-03-15] user said: "I\'m kinda struggling to balance my energy peaks with my sleep schedule, I aim for 7 hours nightly but sometimes my kids disrupt it, how can I make sure I\'m blocking my creative work effectively in the morning when my energy peaks, rather than multitasking throughout the day? ->-> 1,17"  <stated preference: respect it in the answer>\n- [2024-03-15] assistant said: "1. **Time Savings**: By batching emails once a week, you can save time that would otherwise be spent checking and responding to emails throughout the day."  <duration: a week>\n- [2024-09-05] assistant said: "- **Financial Tracking:** If needed, she can assist with tracking expenses and managing budgets, particularly for items like the $25,000 commercial shoot or other financial decisions."  <amount: $25,000>\n- This question needs: record (the record that states it). Receipts found: complete.'

DRAFT_EVENT_ORDERING = '1. Training Michele internally instead of a $1,500 freelance editor, saving $900 (Mar 15, 2024)\n2. A $120 dinner at The Coral Reef for four guests after finishing casting (May 2, 2024)\n3. Raising the post-production software budget from $5,000 to $6,200 as of May 2, 2024\n4. Meal prepping on Sundays, saving $50 a week on takeout (Jul 10, 2024)\n5. Approving $3,500 for Facebook and Instagram ads and rejecting a $5,000 influencer campaign (Nov 20, 2024)'


CASES = {
    "knowledge_update": (QUESTION_KNOWLEDGE_UPDATE, PACKET_KNOWLEDGE_UPDATE, DRAFT_KNOWLEDGE_UPDATE),
    "multi_session_reasoning": (QUESTION_MULTI_SESSION, PACKET_MULTI_SESSION, DRAFT_MULTI_SESSION),
    "summarization_14": (QUESTION_SUMMARIZATION_14, PACKET_SUMMARIZATION_14, DRAFT_SUMMARIZATION_14),
    "summarization_6": (QUESTION_SUMMARIZATION_6, PACKET_SUMMARIZATION_6, DRAFT_SUMMARIZATION_6),
    "event_ordering": (QUESTION_EVENT_ORDERING, PACKET_EVENT_ORDERING, DRAFT_EVENT_ORDERING),
}
WHOLE_ANSWER_CASES = ("knowledge_update", "multi_session_reasoning", "summarization_14")
MID_ANSWER_CASES = ("summarization_6", "event_ordering")

#: The memory channel is the fix under test, so these tests must also run on a tree without
#: it: `evidence_text` is forwarded only when the guard accepts it, and every still-fires
#: case then measures exactly the pre-fix behaviour (the figure is not in the memory).
import inspect as _inspect

_HAS_EVIDENCE = "evidence_text" in _inspect.signature(
    unobserved_live_value_claims
).parameters


def _kinds(text, *, user_turn_text="", evidence_text=""):
    if _HAS_EVIDENCE:
        return unobserved_live_value_claims(
            text, user_turn_text=user_turn_text, evidence_text=evidence_text
        )
    return unobserved_live_value_claims(text, user_turn_text=user_turn_text)


def _replace(text, *, user_turn_text="", evidence_text=""):
    if _HAS_EVIDENCE:
        return replace_unobserved_live_claims(
            text, user_turn_text=user_turn_text, evidence_text=evidence_text
        )
    return replace_unobserved_live_claims(text, user_turn_text=user_turn_text)


@pytest.mark.parametrize("case", sorted(CASES))
def test_memory_figure_answer_survives_the_guard(case):
    """With the memory packet the turn carried, the answer is NOT replaced (fix target)."""
    question, packet, draft = CASES[case]
    delivered = _replace(draft, user_turn_text=question, evidence_text=packet)
    assert delivered == draft, delivered


@pytest.mark.parametrize("case", WHOLE_ANSWER_CASES)
def test_base_whole_answer_replacement_reproduced_without_evidence(case):
    """Without the memory channel the measured failure stands: whole answer -> the notice."""
    question, _packet, draft = CASES[case]
    assert unobserved_live_value_claims(draft, user_turn_text=question)
    delivered = replace_unobserved_live_claims(draft, user_turn_text=question)
    assert delivered.startswith(NOTICE_OPENER)


@pytest.mark.parametrize("case", MID_ANSWER_CASES)
def test_base_notice_insertion_reproduced_without_evidence(case):
    """Without the memory channel the notice is inserted mid-answer (convicted sentence gone)."""
    question, _packet, draft = CASES[case]
    delivered = replace_unobserved_live_claims(draft, user_turn_text=question)
    assert NOTICE_OPENER in delivered
    assert delivered != draft


def test_bitcoin_price_without_memory_still_gets_the_notice():
    question = "What is Bitcoin trading at right now?"
    packet = PACKET_KNOWLEDGE_UPDATE  # a salary memory; no Bitcoin figure anywhere in it
    reply = "Bitcoin is trading at $97,400 right now, up 1.2% today."
    assert _kinds(reply, user_turn_text=question, evidence_text=packet)
    delivered = _replace(reply, user_turn_text=question, evidence_text=packet)
    assert delivered.startswith(NOTICE_OPENER)


def test_salary_figure_absent_from_memory_still_gets_the_notice():
    question = "What's my current salary?"
    packet = PACKET_KNOWLEDGE_UPDATE  # holds $98,000 / $12,000, never $115,000
    reply = "Your current salary is $115,000 a year."
    assert _kinds(reply, user_turn_text=question, evidence_text=packet)
    delivered = _replace(reply, user_turn_text=question, evidence_text=packet)
    assert delivered.startswith(NOTICE_OPENER)


def test_fx_conversion_with_made_up_rate_still_gets_the_notice():
    question = "Convert 100 USD to EUR."
    packet = PACKET_KNOWLEDGE_UPDATE  # no EUR figure in the memory
    reply = "100 USD is currently about 92 EUR."
    assert _kinds(reply, user_turn_text=question, evidence_text=packet)
    delivered = _replace(reply, user_turn_text=question, evidence_text=packet)
    assert NOTICE_OPENER in delivered


def test_memory_figure_match_reads_currency_notation_across_forms():
    """The figure match is number + currency, not surface text: GBP 90,000 == £90,000."""
    packet = PACKET_MULTI_SESSION  # states the offer as "\u00a390,000 salary offer"
    reply = "Your current UK offer is GBP 90,000 a year."
    assert unobserved_live_value_claims(reply, user_turn_text="What is my current salary?")
    assert not _kinds(
        reply, user_turn_text="What is my current salary?", evidence_text=packet
    )


def test_inspect_unsourced_current_claim_keeps_memory_figure_answer_supported():
    """The turn-level seam agrees: a salary answer whose figures the admitted memory states
    is not an unsupported current claim (otherwise `unverified_current_answer` replaces it)."""
    from core.bootstrap_context import admitted_capsule_evidence_text
    from core.unsourced_current_claim import inspect_unsourced_current_claim

    source_context = {
        "runtime_session_id": "chat-20261030-knowledge-update",
        "admitted_capsule_evidence": {
            "text": PACKET_KNOWLEDGE_UPDATE,
            "chat_id": "chat-20261030-knowledge-update",
            "source": "canonical_runtime_transcript",
        },
    }
    assert admitted_capsule_evidence_text(source_context)
    verdict = inspect_unsourced_current_claim(
        answer=DRAFT_KNOWLEDGE_UPDATE,
        requires_current=True,
        session_id="chat-20261030-knowledge-update",
        source_context=source_context,
        user_turn_text=QUESTION_KNOWLEDGE_UPDATE,
    )
    assert not verdict.unsupported
