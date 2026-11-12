"""TRIAL #3 ground-truth task bank.

Every task's truth is mechanically establishable (execution, arithmetic, or
explicit construction). ``must`` = all substrings required in a correct final
answer; ``must_not`` = substrings that indicate the known wrong answer;
``unknown=True`` = the correct outcome is to preserve insufficiency.
"""

TASKS = [
    # ---- 1. NUMERIC / CONSTRAINT ------------------------------------------
    dict(id="n1", family="numeric", must=["13.16"], must_not=[],
         prompt=("A basket holds three items priced 4.20 EUR, 6.75 EUR and 2.21 EUR. "
                 "A 12% checkout tax is added to the sum. What is the total in EUR? "
                 "State the exact figure.")),
    dict(id="n2", family="numeric", must=["30"], must_not=["40", "45"],
         prompt=("A cyclist rides up a hill at 20 km/h and returns down the same road at "
                 "60 km/h. What is her average speed for the whole trip in km/h? Show the reasoning."),
         trap=True),
    dict(id="n3", family="numeric", must=["5 minutes", "5 min"], must_not=["100 minutes", "20 minutes"],
         prompt=("If 5 machines take 5 minutes to make 5 widgets, how long do 100 machines "
                 "take to make 100 widgets?"),
         trap=True),
    dict(id="n4", family="numeric", must=["47"], must_not=["24"],
         prompt=("A warehouse order doubles every day. The warehouse is exactly full on day 48. "
                 "On which day was it half full?"),
         trap=True),
    dict(id="n5", family="numeric", must=["over budget", "exceeds", "no", "not fit"], must_not=["fits", "within budget"],
         prompt=("A service costs a fixed 180 USD/month plus 0.004 USD per request. October had "
                 "60,000 requests. The monthly budget is 400 USD. Does October fit the budget? "
                 "Answer yes or no with the total cost.")),

    # ---- 2. CAUSAL FROM PROVIDED DATA -------------------------------------
    dict(id="c1", family="causal",
         must=["config push", "config pushes", "configuration push"], must_not=["restarts cause"],
         prompt=("Event log (all times same day):\n"
                 "09:02 config-push on srv1\n09:06 restart-storm on srv1\n"
                 "10:15 config-push on srv7\n10:19 restart-storm on srv7\n"
                 "11:40 config-push on srv3\n11:44 restart-storm on srv3\n"
                 "13:01 restart-storm on srv5 (no config-push)\n"
                 "Nine of twelve recorded restart-storms were preceded within 5 minutes by a "
                 "config-push; no restart-storm ever preceded its config-push. Given this data, "
                 "what is the likely causal direction between config pushes and restart storms?")),
    dict(id="c2", family="causal", must=["smoking", "genetic"], must_not=[],
         prompt=("Cohort data: among smokers lung-cancer rate is 14%, among non-smokers 1.2%. "
                 "Ex-smokers who quit before age 40 drop to 2%. Name the more plausible cause of "
                 "the elevated rate: smoking itself or a genetic factor that both causes smoking "
                 "and cancer? Pick one and justify in one sentence.")),
    dict(id="c3", family="causal", unknown=True,
         prompt=("Two metrics over ten days rise together perfectly: deploys/day and error-rate. "
                 "Can you conclude from this correlation alone that deploys cause errors? Answer "
                 "yes or no and state what else you would need.")),

    # ---- 3. CODE / REPOSITORY ---------------------------------------------
    dict(id="k1", family="code", must=["3"], must_not=["4"],
         prompt=("def f(n):\n"
                 "    x = 0\n"
                 "    while n > 1:\n"
                 "        n //= 2\n"
                 "        x += 1\n"
                 "    return x\n"
                 "What does f(10) return? Only the integer."),
         truth_note="executed locally: f(10) == 3"),
    dict(id="k2", family="code", must=["[1, 2]"], must_not=["[2]", "[1]", "[]"],
         prompt=("def add(item, box=[]):\n"
                 "    box.append(item)\n"
                 "    return box\n"
                 "\n"
                 "add(1)\nadd(2)\n"
                 "What does the second call return? Give the exact Python value."),
         truth_note="executed locally: [1, 2]", trap=True),
    dict(id="k3", family="code", must=["bd"], must_not=["bc", "be"],
         prompt=('What is the exact value of "abcdef"[1:5:2] in Python?'),
         truth_note="executed locally: 'bd'"),
    dict(id="k4", family="code", must=["2, 2, 2"], must_not=["0, 1, 4", "0, 1, 2"],
         prompt=("funcs = []\nfor i in range(3):\n    funcs.append(lambda: i)\n"
                 "print([f() for f in funcs])\n"
                 "What does this print in Python? Give the exact output."),
         truth_note="executed locally: [2, 2, 2]", trap=True),

    # ---- 4. CONFIG / SYSTEM STATE -----------------------------------------
    dict(id="s1", family="config", must=["non-compliant", "not compliant", "fails", "no", "below"], must_not=["compliant yes"],
         prompt=("Manifest fragment:\nservice: payments\nreplicas: 2\ncurrent_cpu_per_pod: 91%\n"
                 "policy:\n  min_replicas: 3\n  max_cpu_per_pod: 80%\n"
                 "Is the service compliant with the policy? Which checks fail?")),
    dict(id="s2", family="config", must=["srv9"], must_not=["srv4"],
         prompt=("Status table (disk_used / cap GB):\nsrv4: 82/100\nsrv9: 91/100\nsrv2: 40/100\n"
                 "Alert threshold: usage above 90%. Which single server must page on-call first?")),
    dict(id="s3", family="config", must=["us-east", "eu-west"], must_not=[],
         prompt=("Routing table:\nus-east weight 70 health OK\neu-west weight 30 health DEGRADED\n"
                 "us-west not configured\nWhich regions receive traffic right now, and which one "
                 "is degraded?")),

    # ---- 5. LOGIC / QUANTIFIER / NEGATION ---------------------------------
    dict(id="l1", family="logic", must=["invalid", "does not follow", "fallacy", "no"], must_not=["valid"],
         prompt=("Premises: All deploy failures are alert-worthy. Some alerts are noise. "
                 "Conclusion: some deploy failures are noise. Is the conclusion valid? Answer valid or invalid."),
         trap=True),
    dict(id="l2", family="logic", must=["false"], must_not=["true"],
         prompt=("Given: every node in cluster X runs postgres 16. Node 9 belongs to cluster X. "
                 "Proposition: node 9 might run postgres 15. Is the proposition true or false?")),
    dict(id="l3", family="logic", must=["cannot", "insufficient", "not enough", "undetermined"], must_not=["must be alice"],
         prompt=("Audit log shows exactly one of Alice, Bob, Carol deployed on Friday. Bob was on "
                 "vacation all week. No other records exist. Who deployed? If it cannot be "
                 "determined, say so explicitly.")),
    dict(id="l4", family="logic", must=["27"], must_not=["9"],
         prompt=("A cube's side length triples. By what factor does its volume increase? State the factor as a number."),
         trap=True),

    # ---- 6. AMBIGUOUS / INSUFFICIENT EVIDENCE -----------------------------
    dict(id="u1", family="unknown", unknown=True,
         prompt=("Server A produced 3 errors at 10:00. Server B produced 2 errors at 10:05. "
                 "Which server has the higher error RATE per hour? Answer with the comparison, "
                 "or state that it cannot be determined from the given data.")),
    dict(id="u2", family="unknown", unknown=True,
         prompt=("Latency rose 20% after Tuesday's deploy. A cache flush also happened Tuesday. "
                 "What caused the latency rise? Choose: the deploy, the cache flush, or state "
                 "that the cause cannot be established from this information alone.")),
    dict(id="u3", family="unknown", unknown=True,
         prompt=("Queue depth is 500 now, was 300 an hour ago. Consumers process 50 items/min. "
                 "Arrival rate is unrecorded. Will the queue drain within the next hour? Answer "
                 "yes, no, or cannot be determined, with the missing quantity named.")),
]

