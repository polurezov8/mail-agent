# Mail triage intake

Fill in the lists below. Concrete examples, not abstract categories. Empty = "I don't have any."

Format:
- Emails: full address (`name@domain.com`) or domain (`@domain.com`) or wildcard (`*@subdomain.example.com`).
- Subjects/keywords: just the substring; case-insensitive.
- Save the file, then ping the agent: "intake filled".

---

## 1. VIP allowlist — always surface as RESPOND

People/domains whose mail must never be auto-anything. Even one missed mail from here is unacceptable.

### People (specific emails)
- 
- 
- 
- 

### Domains (group rules)
- 
- 
- 

### Notes
> Anything special, e.g. "but if subject contains 'newsletter' demote to ignore":



---

## 2. Notify bucket — surface in daily digest, no urgency

System notifications, calendar, alerts, monitoring. You want to see them but not get pinged immediately.

### Already covered by header rules (no action needed unless you disagree)
- ☑️ Calendar invites (`Content-Type: text/calendar`)

### Add to notify bucket
- 
- 
- 
- 

### Specific senders
- e.g. `alerts@sentry.io` → notify
- 
- 

### Subjects / patterns
- e.g. subject contains "incident" → notify
- 
- 

---

## 3. Ignore bucket — silent + auto-mark-read

Mail you never want to see in the inbox. Be aggressive: anything truly junk goes here.

### Already covered (no action needed)
- ☑️ List-Unsubscribe header → newsletters rule
- ☑️ github.com → github_notifications

### Specific senders to add
- e.g. `noreply@medium.com`
- 
- 
- 

### Domains to add
- 
- 
- 

### Subject patterns
- e.g. "Your order shipped" 
- 
- 

### Are GitHub notifications currently right? (auto-marked silent)
> Keep ignore / change to notify:



### LinkedIn — keep ignore?
> 



---

## 4. Natural-language rules (LLM-driven)

Patterns too nuanced for header matching. The LLM sees these at classify time.

Format: one rule per line, plain English. Include bucket assignment.

Examples to copy/adapt:
- "Any mail from a real human asking me a question directly → respond"
- "Mention of our company's revenue, growth, or board → respond"
- "Cold sales pitches from vendors → ignore unless they reference a current product"
- "Order confirmations / shipping notifications → ignore + auto-mark"
- "Mail forwarded by my assistant → notify"

### Your rules
1. 
2. 
3. 
4. 
5. 

---

## 5. Tuning preferences

### Risk tolerance (pick one)
- [ ] **Conservative** — better to leave unread than wrongly auto-mark. Default; current `auto_mark_min_confidence = 0.95`.
- [ ] **Balanced** — accept some false-positives on auto-mark for fewer manual touches. (`0.90`)
- [ ] **Aggressive** — minimize inbox, accept occasional missed mail. (`0.85`)

### Slack delivery cadence (already set: tiered)
- Respond → realtime DM
- Notify → digest, currently every poll (every 30 min). Change to **daily 9am only**?
  - [ ] Keep current (every poll)
  - [ ] Switch to daily 9am only

### Any times you do NOT want Slack pings?
> e.g. "no respond pings 8pm-7am" / "no pings on weekends":



---

## 6. Free-form

Anything else about how you triage / what surprises you / what existing apps got wrong for you:




---

When done — save file, tell the agent "intake filled" + path is `config/triage_intake.md`.
