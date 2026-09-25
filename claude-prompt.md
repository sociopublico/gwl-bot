UNGA Speech Coding — Project Prompt & Methodology
This document captures the full set of instructions, conventions, and corrections established over the course of this project, so the coding process can be replicated — by another LLM session, another coder, or a future phase of this project — with consistent results.


1. Project overview
Task: extract and classify UN General Assembly speeches (plain .txt transcripts) against a fixed codebook of stance indicators, using an LLM as a first-pass coder, followed by human validation from two independent reviewers.

Phase 1 (complete): 40 speeches, coded manually in-chat, output as a full spreadsheet with metadata.

Phase 2 (in progress, Sept 22–28): 192 additional speeches for a new set of countries, ~38/day over 5 working days, output as two CSVs only (no metadata — see Section 2).


2. Do we still need speaker/country metadata?
No — not for Phase 2. The two final outputs (Indicators.csv and Emerging_Priorities.csv) carry only id_speech as the link back to a speech, and that comes directly from the filename of each .txt file, which is already named with its id_speech. Neither CSV has a country, speaker, region, or date column. So sending a separate metadata table is unnecessary overhead for this phase — just send the .txt files.

(If a future phase needs the fuller UNGA_speech_coding.xlsx — with its Metadata and Codebook sheets, used in Phase 1 — that file's Metadata sheet does need a proper metadata table, since several of its fields (region, subregion, UN Security Council / G20 membership, speaker pronouns/gender/level) genuinely can't be reconstructed from the speech text alone, and guessing them is riskier than not having them. See the Phase 1 schema below for reference if that's ever needed again.)

Phase 1 Metadata schema, for reference:

id_speech, num_of_appearance, date_time, iso_country, country, region, subregion,

Status, security_council_member, g20_member, speaker_name, speaker_level,

speaker_pronouns, speaker_gender, statement_web_url, transcript_url

Rule if this is used again: if a pasted metadata row includes extra ad hoc fields beyond these 16 (e.g. file format, language, a third status-like column), omit them rather than guessing where they belong.


3. Codebook (8 indicators, in 3 clusters)
Cluster: Gender and women's leadership
gender_equality_position — What position does the speech take on gender equality? | Code | Option | Definition | |---|---|---| | 0 | No Mention | Speech does not reference gender equality, women's rights, gender imbalance, gender inclusion, or related topics. | | 1 | Positive / Supportive | Any expression of support, endorsement, or policy commitment regarding gender equality — including denouncing gender imbalance, calling for gender inclusion, or endorsing the Women, Peace and Security agenda. The exact phrase "gender equality" is not required. | | 2 | Mixed / Ambiguous | Discusses gender equality/women's rights but doesn't establish a clear position, contains both supportive and contradictory elements, or highly qualified language. | | 3 | Negative / Pushback | Any expression of opposition, rejection, or conditional limitation based on culture, sovereignty, or tradition. |

women_leadership_position — What position does the speech take on women's leadership and participation in decision-making? | Code | Option | Definition | |---|---|---| | 0 | No Mention | No substantive reference to women's participation, representation, or leadership in decision-making. | | 1 | Positive / Supportive | Explicitly supports women's participation, representation, or leadership in decision-making. | | 2 | Mixed / Ambiguous | Mentions without clear endorsement, or combines support with significant qualifications. | | 3 | Negative / Pushback | Explicitly rejects, restricts, or questions women's participation or leadership. |

women_multilateral_leadership_position — What position does the speech take on women's representation or leadership within international/multilateral institutions, including the UN? Same 0/1/2/3 structure as above, scoped specifically to multilateral/UN institutions (not domestic government).
Cluster: SG election
(Originally grouped under "Gender and women's leadership"; split into its own cluster partway through Phase 1 — see Section 7.)

sg_selection_position — What position does the speech take on the Secretary-General selection process? | Code | Option | Definition | |---|---|---| | 0 | No Mention | Does not refer to the selection/appointment/candidacy of the UN Secretary-General (see Rule 6.F: PGA and other UN elections do not count). | | 1 | Positive / Supportive | Supports the SG selection process or principles associated with it (transparency, inclusiveness, merit, regional rotation, etc.). | | 2 | Mixed / Ambiguous | Discusses the SG process but doesn't establish a clear position. | | 3 | Negative / Pushback | Criticizes the current SG process or calls for changes to how the SG is selected. |

sg_selection_position_gender — What position does the speech take on the possibility of a female Secretary-General? Same 0/1/2/3 structure, scoped to gender and the SG role specifically.
Cluster: UN reform
un_reform_position — What position does the speaker take regarding reform of the United Nations and its institutions? Standard 0/1/2/3 structure. Positive (1) = supports or calls for UN reform/change/transformation (UN80, Security Council without veto, "the UN must change/transform"). Negative (3) = opposes or rejects reform. A crisis diagnosis plus "it needs to be transformed" is Positive, not Negative. A call to renew, change, or reorient the UN or this Organization is Positive even with no named mechanism. Critique of inaction on a crisis, or reform only of the financial architecture, is No Mention (0), not Mixed — see Rule 6.J.
Cluster: Multilateralism
current_multilateralism_position — What position does the speaker take regarding the current multilateral system? Standard 0/1/2/3 structure. See Section 6.B for a critical coding rule on this indicator.

future_multilateralism_position — What position does the speaker take regarding the future direction of international cooperation? | Code | Option | Definition | |---|---|---| | 0 | No Mention | No substantive discussion of how international cooperation should evolve. | | 5 | Preservation / Strengthening | Advocates preserving/defending/strengthening the existing system without calling for fundamental changes. | | 2 | Mixed / Ambiguous | Expresses competing or unclear visions for the future. | | 6 | Transformation | Calls for adapting, renewing, modernizing, or reforming multilateral cooperation while broadly retaining the existing system. |

Note: this indicator's codes are non-sequential (0, 5, 2, 6) — inherited as-is from the original codebook design, not an error.

Known gap: this indicator has no option for a speech that explicitly rejects reliance on the multilateral system in favor of unilateral/bilateral action (e.g. a speech critical of the UN's relevance without proposing reform). Such cases were coded "No Mention" for lack of a fitting category and flagged in coder_notes. Worth adding a 5th option before scaling further.

A 9th indicator, emerging_priorities (open-ended thematic tagging), was originally proposed as part of the core codebook but was moved to its own lightweight, non-priority tab instead — see Section 4.


4. Output 1 — Indicators.csv
Columns: id_speech, extract_id, cluster, indicator_name, code, option, textual_extract, coder_notes, coding_source, model, date_coded

extract_id: {id_speech}_{indicator_name}, e.g. M_3_un_reform_position — a unique, human-readable reference per row for audit purposes.
coding_source / model / date_coded: audit trail — who/what produced this code and when, so results stay traceable if the coding method or model changes later.
coder_notes: flags any row where the code is genuinely borderline, inferential, or depends on a judgment call the human reviewers should specifically weigh in on. Left blank for clear-cut cases. See Section 6.D for examples.


5. Output 2 — Emerging_Priorities.csv (lightweight, non-priority)
Columns: id_speech, id_extract, emerging_topic, textual_extract

One row per topic present in the speech, not one row per speech — a speech touching 5 themes gets 5 rows.
emerging_topic must come from a fixed, closed list, not free text: Climate change, AI / digital transformation, Peace and security, Development / SDGs, Inequality, Financing / debt, Human rights, Migration, Global health, Food security, Technology / digital divide, Other.
textual_extract is a short single line only — no position/stance judgment, no exhaustive multi-passage extraction like Indicators.csv gets.
Explicitly not a priority for the two human validators — it should not add review overload. If time is tight, protect the depth of the 8 core indicators over this tab.


6. Coding rules established through iteration
These are corrections made after reviewing early coded speeches in Phase 1 — apply them from the start rather than rediscovering them.

A. Textual extraction must be exhaustive and unabridged, not just "enough to justify the code."

Scan the entire speech for every passage relevant to an indicator, not just the first or most obvious one.
Quote only a contiguous verbatim span that bears on that indicator. Do not bridge non-adjacent sentences with "...": intervening text means they are separate quotes, or omit the one that does not bear on the indicator. A paraphrase or a passage about another indicator is not evidence, so omit it.
When more than one passage applies, number them (1) ... (2) ... (3) ... within the cell, in the order they appear in the speech.
Even a single citation gets a (1) prefix, for formatting consistency.
"No mention" cells (code 0): leave textual_extract empty. Do not write "No mention of …" or any placeholder sentence.

B. current_multilateralism_position: distinguish nostalgia from a genuine present-tense claim. If a speech pairs (a) a positive statement about the UN's past/founding-era achievements with (b) negative statements about its present state, code this Negative (3), not Mixed. Praising what the UN was or achieved historically does not offset criticism of what it currently is. Only a present-tense positive claim about what the system currently does counts toward Positive — e.g. "the UN remains essential," "without the WFP, 125 million people would lack food assistance," not "the UN was created to..." or "embodied the great hope that...".

Also code Negative (3), not Mixed, when the speech says or clearly implies that the past/progress was better or is "under threat"; that the present system has lost credibility, effectiveness, or capacity (systemic challenges putting credibility to the test, being "eaten away"); or that the future will restore/fix it ("restore credibility," "with her/new leadership the United Nations will be able to..."). Restoration-of-the-UN language is a diagnosis that the current system is failing. A single present-tense compliment ("the UN remains a pillar," "multilateralism is a necessity") does not make the code Mixed if those threat/crisis/restore diagnoses are also present. Mixed is only for genuine competing evaluations of how the system currently functions.

C. Internal consistency across related indicators. If sg_selection_position_gender has any evidence (non-zero code), sg_selection_position cannot be coded "No Mention" for that same speech — gender-specific SG discussion necessarily means the speech discusses the SG selection process itself (it can land on a different code, just never 0 when the gender one is non-zero). gender_equality_position cannot be 0 when sg_selection_position_gender is Positive: an explicit woman SG is a gender-equality position. Mixed on that indicator does not force gender_equality off 0. Women's multilateral leadership is a subset of women's leadership: if women_multilateral_leadership_position is non-zero, women_leadership_position cannot be 0; if that passage is the only evidence, use the same code. A woman leading in a multilateral body is women's leadership. Praise of only the speaker's own wife or first lady, with no claim about women in general, is Mixed on women_leadership_position and women_multilateral_leadership_position: one named woman is not a position on women's leadership.

D. Borderline/inferential codes must be flagged, not silently decided. Any time a code depends on reading between the lines — e.g. inferring a "gender equality" position from a "defending the traditional family" passage that never uses the words "gender" or "women's rights" — write a note in coder_notes explaining the inference and naming the ambiguity, rather than presenting it as a clean, settled call. These are exactly the rows the two blind human validators should prioritize.

E. Data-quality issues found in source files should be surfaced, not silently worked around. Examples encountered in Phase 1: a source .txt containing two concatenated speeches; a metadata row with a speaker's gender/pronouns apparently mismatched to their real identity; a country's region field containing an obviously wrong value. In each case: use only the correct/relevant portion for coding, but explicitly flag the anomaly rather than quietly patching around it — these often indicate a systematic issue worth checking across the rest of the corpus.

F. sg_selection_position is only about the UN Secretary-General. Do not code congratulations or comments on any other UN election as SG selection. In particular: election of the President of the General Assembly (PGA) — e.g. congratulating Annalena Baerbock or any other PGA — is not SG selection. Same for ECOSOC president, Security Council presidency, ICJ judges, or other officers. "Election" + a woman's name is not enough. Praise or "building on the achievements" of the sitting Secretary-General is not selection (code 0, do not quote it): naming the incumbent is not a position on how the office is filled. Selection is the appointment, the process, a candidacy, a nomination, an explicit next or new Secretary-General, or the fact that no woman has held the office. sg_selection_position_gender is Positive when the speech, read in context, calls for or welcomes a woman Secretary-General. This includes explicit wording ("a woman", "she", a named woman candidate), and also a passage about women's representation that then asks for it to be reflected in the next Secretary-General or "at the highest level": the referent is women even without the word "woman." Lamenting that the UN has never been led by a woman Secretary-General (e.g. "eight decades later, the United Nations has yet to be led by a woman as Secretary-General") is also Positive, and it counts as a position on SG selection, so sg_selection_position cannot be 0 for that speech. "Her or him", or gender balance listed as one criterion among others, with no push toward a woman, stays Mixed: naming gender is not a proposal to elect a woman. A PGA mention in the same passage excludes only the PGA sentence, not a following statement about the Secretary-General.

G. gender_equality_position: do not require the words "gender equality" or "women's rights." Code Positive (1) when the speech denounces gender imbalance (including at the UN or in the SG office); calls for gender inclusion or the effective inclusion of women as a gender/rights agenda; or endorses the Women, Peace and Security (WPS) agenda / "women, peace and security" as indispensable to peace.

Stated opportunities, inclusion, or benefit for women and girls → Positive: that is a gender-equality claim even without the words "gender equality".
Women and girls named as targets of hate, discrimination, or misogyny the speaker opposes → Positive: the harm is gendered and the speaker takes a side.
Women and children listed as victims of war, drugs, famine, or environmental harm, with no rights or discrimination claim → No Mention.
Opposition to transgender people or gender identity → Negative, never Positive. Praise of only the speaker's own wife or first lady is not a general position; together with that opposition → Mixed. The code must match the stance described in coder_notes.

H. Scan the whole speech before locking a code. Do not stop at the first "the UN remains a pillar" (multilateralism) or the first protocol congratulation (SG). Later sentences about threat, restoration, gender imbalance, WPS, UN80, or an actual SG candidacy often change the code.

I. un_reform_position: calling the UN a crisis that "needs to be transformed/changed/reformed" is Positive (1), not Negative. Negative is pushback against reform (the UN should stay as it is; reform is unwanted). Support for UN80 ("UN-80 initiative," strengthening efficiency/effectiveness of the organization via that initiative), a new Security Council without vetoes, or "the UN should begin its change" is Positive.

J. un_reform_position vs critique of the UN: a call to renew, change, transform, or reorient the UN or "this Organization" is Positive even with no named mechanism: asking the institution to change is reform. Critique of inaction on a crisis, or reform only of the financial architecture, IMF, or World Bank, stays No Mention. That critique may belong on current_multilateralism_position instead.

K. If un_reform_position is Positive (1), future_multilateralism_position must be Transformation (6), not Preservation / Strengthening (5). Institutional reform is a change to how multilateralism will work. UN80 support is reform + transformation even if the speaker also says they "remain committed to multilateralism" or want to "strengthen this institution."


7. Structural changes made mid-project
sg_selection_position and sg_selection_position_gender were moved out of the "Gender and women's leadership" cluster into their own cluster, "SG election."
The original 9-indicator design (with emerging_priorities as a single-code field) was split: emerging_priorities became its own separate, lightweight, non-priority tab, because a speech can raise multiple themes at once and forcing it into a single code loses information.


8. Workflow instructions
No API access assumed — this methodology runs entirely through an LLM chat interface (the model reads each speech and codes it directly in conversation), not via an automated script.
Batch size: ~4–6 speeches at a time, each with a full close read of the source text (not keyword/grep-based scanning). A batch of 19 was tried once and caused a measurable quality drop — thinner extracts, reliance on keyword scanning for some speeches. Smaller batches maintain the standard.
Within one uploaded batch, work through all speeches in sequence in a single turn without pausing for permission between internal chunks — but still self-pace so each speech gets the same depth of attention, with a short checkpoint line after each chunk.
After finishing, briefly summarize what stood out (notable codes, borderline calls, data issues) rather than just stating counts.
Daily workflow for a multi-day run (e.g. the Sept 22–28 batch of 192 speeches)
Start a new chat each day. Files do not carry over between chats — only saved project memory does (the codebook, rules, and conventions in this document). So each day:

Open a new chat and paste in the day's prompt (template below).
Attach the current Indicators.csv and Emerging_Priorities.csv to continue from, plus that day's ~38 .txt files (filenames = id_speech).
The model processes the full batch in one turn, in internal 4–6-speech chunks.
It returns the two CSVs, appended to what was attached — not a fresh file.

Standing prompt template (fill in the day number each time):

New batch for the UNGA speech coding project (day [1/2/3/4/5] of 5, ~38 speeches/day, running through Sept 28). Please pull up the project's saved methodology before we start.

Attached: the current Indicators.csv and Emerging_Priorities.csv to continue from, plus 38 .txt speech files (filenames = id_speech). No metadata needed — id_speech from the filename is the only link required for these two outputs.

Please process the full batch in this one turn — pace yourself internally into 4-6-speech chunks with a full close read each (not keyword scanning), giving a short checkpoint line after each chunk. Build the Emerging_Priorities tab alongside the core 8 indicators this time, not as a separate pass.

At the end, give me the refreshed Indicators.csv and Emerging_Priorities.csv only (full columns, newlines flattened, per our established format) — appended to what I attached, not a fresh file starting over.

Flag anything borderline in coder_notes as usual, and flag any data-quality issues in the source files rather than silently working around them.


9. Human validation design (for methodological rigor)
Two independent human reviewers code a stratified sample of speeches (recommended ~20–30%, stratified by region), each blind to the LLM's codes and to the other reviewer's codes.
Disagreements between the two human reviewers are adjudicated (discussion, or a third senior reviewer) to produce a "gold standard" code for that sample.
Compute inter-rater reliability two ways: human-vs-human (validates the codebook itself is usable) and LLM-vs-gold-standard (validates the automated first pass). Given the categorical, not strictly ordinal, nature of the codes, prefer Krippendorff's alpha (nominal) or simple % exact agreement + a confusion matrix per indicator, rather than a metric assuming ordering.
Any indicator with low LLM-vs-gold-standard agreement should default to full human coding rather than trusting the automated pass for that indicator specifically.


10. Output format conventions
Indicators.csv: all 11 columns listed in Section 4.
Emerging_Priorities.csv: all 4 columns listed in Section 5.
Both exports must have embedded newlines flattened (replace with " | ") before writing, so every row is exactly one physical line. Multi-line quoted CSV fields break tools like Google Sheets' IMPORTDATA, which doesn't parse quoted embedded newlines correctly and can misalign rows/columns badly enough to trigger cell-limit errors on import.


11. Daily prompt:

New batch for the UNGA speech coding project (day 1 of 5, ~38 speeches/day,
running through Sept 28). Please pull up the project's saved methodology
before we start.

Attached: the current Indicators.csv and Emerging_Priorities.csv to continue
from, plus 38 .txt speech files (filenames = id_speech). No metadata needed —
id_speech from the filename is the only link required for these two outputs.

Please process the full batch in this one turn — pace yourself internally
into 4-6-speech chunks with a full close read each (not keyword scanning),
giving a short checkpoint line after each chunk. Build the Emerging_Priorities
tab alongside the core 8 indicators this time, not as a separate pass.

At the end, give me the refreshed Indicators.csv and Emerging_Priorities.csv
only (full columns, newlines flattened, per our established format) —
appended to what I attached, not a fresh file starting over.

Flag anything borderline in coder_notes as usual, and flag any data-quality
issues in the source files rather than silently working around them.
