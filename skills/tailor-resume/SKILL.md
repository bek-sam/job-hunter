---
name: tailor-resume
description: Rules for tailoring a one-page new-grad / intern software resume and cover letter to one job posting - JD extraction schema, bullet selection rubric, allowed rewording, ATS parse checks, cover-letter rules. Use when building or reviewing a resume or cover letter for a specific job.
---

# Tailor a resume to one job

This is the rulebook the Job Hunter pipeline follows (`jobhunter/gen_v6.py`, `reword.py`, `resume_check.py`,
`packet.py`). `packet.py` loads the **Cover letter rules** section below into its prompt, so editing it changes every
new letter. To use it in Claude Code too: `ln -s "$PWD/skills/tailor-resume" ~/.claude/skills/tailor-resume`.

## 1. Read the job (JSON, cached per posting)
Extract once per posting (`packet.write`, cached in `data/jd_cache/`):
- `title_exact` - the posting's title without location / req id
- `skills_required`, `skills_preferred` - short canonical names ("PostgreSQL", "REST APIs")
- `knockouts` - hard requirements that screen people out before a human reads: work authorization, graduation
  window, location / on-site, degree, years, clearance
- `seniority` - intern / new grad / entry / mid / senior
- `company_specifics` - 1-3 concrete facts about the team or product from the JD (for the cover letter)

## 2. Pick bullets (no new facts)
- Bullets come only from the Notion bullet bank. Pinned bullets are hand-approved and always win their slot.
- Score: role tag order x3, JD term hits x3 (**required terms count double**), +3 for a number, -3 if under 90
  characters, +4 confirmed result, +5 boost, -4 for "including A, B, C, D" lists.
- No first person, no near-duplicates (>45% shared words), one page, single column.

## 3. Reword (only vocabulary)
Swap at most 3 words of a bank bullet for the job's exact term for the same thing ("Postgres" -> "PostgreSQL").
The code rejects any rewrite that changes a number, adds a word that isn't in the job's own text, changes more than
3 words, or grows by more than 20 characters. Pinned bullets are never reworded. Every change is listed on the
Notion page for review.

## 4. Check the final PDF
- Text extracts, the name and email are in the body (not a Word header), headings read in order, under 2.5 MB, no
  icon-font glyphs.
- Coverage of required terms is reported; missing required terms are listed as gaps (interview prep), and any term
  repeated 4+ times is flagged as stuffing.

## 5. Cover letter rules
- 3 short paragraphs, max 170 words, plain polite English, first person, signed with the candidate's name.
- Paragraph 1 names ONE concrete thing about this team or product taken from the JD (not "your mission",
  "innovative company" or the company's slogan).
- Paragraph 2 maps 1-2 real projects to their needs, and at least one sentence carries a specific proof point (a
  number, a shipped feature, a tool) that also appears on the resume.
- Use the job's exact names for the tools it lists.
- Never mention work authorization, visas, residency, citizenship or sponsorship.
- Banned phrases (generic, read as AI-written): "I am excited to apply", "I am writing to express", "I am thrilled",
  "passionate about", "align perfectly", "aligns perfectly", "perfect fit", "fast-paced environment",
  "leverage my skills", "unique blend", "proven track record", "dynamic team", "hit the ground running",
  "I believe I would be a great", "cutting-edge", "synergy", "delve".

## 6. What not to do
- No invented employers, titles, dates, degrees, metrics, users or deployments.
- Skills (Oct 31 2026 rule): EVERY skill the job description names goes on the resume (`SKILLS_MODE=all`,
  `SKILLS_UNPROVEN_MAX=-1`); then each Skills line is filled to full width with his other skills
  (`gen_v6.fill_pool`: base list -> score.EVIDENCE -> known Notion tech). Bullets still never claim a tool he didn't use.
- No two-column layouts, tables, text boxes or header/footer contact info.
