# How applying works on the major ATSs, for a form-filler that stops before submit (researched 2026-09-24)

**Method.** Four parallel research passes used web search and fetch, live curl calls against public endpoints, Playwright looks at real apply pages, and reading of the vendors' own JavaScript and of open-source auto-apply projects. No accounts were created and nothing was submitted. "Live" means seen on a real company's site on 2026-09-24.

**Test-run incident.** The Playwright browser was shared by two research passes. During the SuccessFactors check, one "Create an account" text click actually landed on an open OpenAI Ashby application page. Nothing was submitted; later work used separate browsers or curl. Two lessons for the filler: give every automation run its own browser, and never locate the thing to click by loose text matching.

---

## Summary table

| ATS | Questions visible before opening the page? | Account needed? | Drafts saved on the server? | Pages in the form | Bot detection | Difficulty (1–5) |
|---|---|---|---|---|---|---|
| **Workday** | **Yes**, no login: job JSON gives a `questionnaireId`, then a public questionnaire endpoint | **Yes, one per employer**; email-verify link on some employers | **Yes** (each step saves; Candidate Home → Drafts) | 6–7: Sign in, My Information, My Experience, Questions (1–3), Voluntary Disclosures, [Self Identify], Review | No real CAPTCHA; an invisible click-overlay check, a hidden trap field, Cloudflare | **5** |
| **Greenhouse** | **Yes**: `boards-api…/jobs/{id}?questions=true` | No (optional MyGreenhouse) | No | 1 page | Invisible reCAPTCHA Enterprise; low score leads to an 8-character emailed security code after Submit | 2 |
| **Lever** | Not via public API (paid v1 has them); **the questions JSON is in the page's HTML** | No | No | 1 page | hCaptcha, Cloudflare | 2 |
| **Ashby** | Public posting API: no; **the site's own GraphQL: yes, no auth** | No | No | 1 page | reCAPTCHA v3 (score-based) | 2–3 |
| **SmartRecruiters** | Official API needs a key; **apply page embeds everything in `window.__OC_CONTEXT__`** | No | No | 2 pages (details, then screening) | **DataDome** + Cloudflare; SEON optional | 3 |
| **iCIMS** | No | Yes (password, per employer) | Yes ("Finish Later") | Many steps, inside an iframe | AWS WAF with CAPTCHA, invisible hCaptcha | 4 |
| **Oracle Cloud HCM** | Step list yes (public REST); question text no | No password, but emailed/SMS 6-digit PIN | Yes (drafts) | Email → PIN → sections → Summary | hCaptcha (per employer), hidden trap field | 4 |
| **SuccessFactors** | No | Yes, plus emailed one-time code | Yes ("Saved Applications") | Sign in, then one long page | reCAPTCHA optional; Cloudflare | 4 |
| **Taleo** | No | Yes, per employer (some guest) | Yes ("Save as Draft") | Many JSF pages | Usually none; many sites offline | 4 |
| **Jobvite** | **Yes**: `preloadedData` JSON in the apply HTML | No | No | Several: main, EEO, OFCCP, prescreen | Invisible reCAPTCHA, Cloudflare | 2 |
| **Rippling** | **Yes**: public `api/v2/board/{slug}/jobs/{id}` | No | No | 1 page | **Cloudflare Turnstile** + session replay | 2 |
| **Gem** | **Yes**: public REST `?questions=true` + GraphQL | No | No | 1 page | hCaptcha, **FingerprintJS Pro, AI fraud agent** | 2 (fill) / high flag risk |
| **Eightfold** | Yes per employer (needs CSRF token from page) | Varies | Partly | 1 long page | Invisible reCAPTCHA, CSRF | 3 |
| **Phenom** | Yes: `POST /widgets` + public CDN form schema | Usually guest | **Yes, every Next saves** | Mirrors backend ATS (often Workday-shaped) | reCAPTCHA configurable, Clarity | 3–4 |
| **LinkedIn Easy Apply** | n/a | Yes | n/a | Modal steps | Extension scanning, rate caps | **Don't automate** (ToS) |

---

## 1. Workday (highest priority)

### (a) Reading questions without logging in (all live, no auth)

| Purpose | Endpoint |
|---|---|
| Job search | `POST https://{t}.wd{N}.myworkdayjobs.com/wday/cxs/{t}/{site}/jobs` with `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}` |
| Job detail | `GET …/wday/cxs/{t}/{site}/job/{externalPath}` |
| Wizard steps and My Experience sections | `GET /wday/calypso/cxs/jobapplication/{t}/jobpostings/{jobPostingId}/applyflowpages?applyDetail=applyManually` |
| **The questions** | `GET /wday/calypso/cxs/common/{t}/questionnaire/{questionnaireId}` |
| Employer settings | `GET /wday/cxs/{t}/{site}/approot`; read `featureFlags.accountVerification` and `requireCandidateAccounts` |
| Password rules and consent text | `GET /wday/calypso/cxs/account/{t}/{site}/account/configuration` |
| CC-305 disability form text | `GET /wday/calypso/cxs/common/{t}/disabilityforms` |

- **Search limits.** `limit` above 20 returns 400. `total` caps at 2000.
- **Job detail** (live, NVIDIA JR2021549) returns `{"jobPostingInfo":{"id":"d5b8…","jobReqId":"JR2021549","canApply":true,"includeResumeParsing":true,"questionnaireId":"2f2764b3a82910064296831695150000"}}`. Some jobs also have `secondaryQuestionnaireId` or `supplementaryQuestionnaireId`.
- **Questionnaire shape:**
  ```json
  {"questions":[{"id":"2f2764b3…","body":"<p>Are you legally authorized…</p>","order":"a","required":true,
    "type":{"id":"c4d9bd57996c10002583f04b9fc30042","descriptor":"Multiple Choice - Single Select"},
    "possibleAnswers":[{"id":"c0ba…","answerText":"Yes","order":"a"},{"id":"…","answerText":"No"}]}]}
  ```
  Follow-up questions nest recursively under `possibleAnswers[].branchingQuestion`.
- **Question type IDs** hard-coded in Workday's front-end (build 2026.38.12), likely the same for every employer:

  | Type | ID |
  |---|---|
  | SingleSelect | `c4d9bd57996c10002583f04b9fc30042` |
  | MultiSelect | `c4d9bd57996c100025ab7d705b150045` |
  | Text | `c4d9bd57996c100025ab7d7f5aed0046` |
  | Numeric | `53d73448d8de1000063827b5cb68003f` |
  | Date | `85fb160ed47910001589e3a427670070` |
  | Upload | `287357c143e210001cddbc6df0719cba` |

- **Mapping answers to page fields.** Each question appears on the page as `primaryQuestionnaire--{questionId}` (or `secondary…`/`supplementary…`).
- **Needs a session.** "How did you hear about us" choices and saved answers load only with an `X-Cxs-Session-Id` header.
- **Step count varies by employer** (live): NVIDIA and Salesforce 6, Intel 7 (adds Self Identify), Workday's own site has two question pages.

### (b) Submit or save APIs

**Official:** Recruiting SOAP API (`Put_Candidate`, `Put_Candidate_Attachment`, `Get_Job_Postings`). Needs employer integration credentials.

**What the web form calls** (undocumented, needs a candidate session):
- Create a draft: `POST /wday/cxs/{t}/jobpostings/{id}/jobapplications`.
- Save sections: `jobapplication/{id}/{resume|name|address|…}`.
- **Submit:** `POST /wday/cxs/{t}/jobapplication/{id}/finalize`.

### (c) Form structure and selectors

**Start page** (`…/job/{loc}/{id}/apply`): `[data-automation-id=autofillWithResume]`, `applyManually`, `useMyLastApplication` (needs sign-in), `applyWithLinkedIn` iframe. The job page's Apply button (`adventureButton`) sometimes ignores scripted clicks; navigate to its `href` instead.

**Account step:**
- Sign-in: `email`, `password`, `signInSubmitButton`, `createAccountLink`.
- Create-account: `email`, `password`, `verifyPassword`, `createAccountCheckbox`, `createAccountSubmitButton`.
- **Hidden trap field:** `input[data-automation-id=beecatcher][name=website]`. Always leave empty.
- Google, LinkedIn, Apple sign-in buttons depend on employer.

**Progress bar:** `ol[data-automation-id=progressBar]` with `li[data-automation-id=progressBarActiveStep]` (text such as "current step 2 of 6 My Information").

**Page containers:** `applyFlowMyInfoPage`, `applyFlowMyExpPage`, `applyFlowPrimaryQuestionsPage` / `…Secondary…` / `…Supplementary…`, `applyFlowVoluntaryDisclosuresPage`, `applyFlowSelfIdentifyPage`, `applyFlowReviewPage`, `alreadyAppliedPage`. Older sites: `contactInformationPage`, `myExperiencePage`, `reviewSubmitPage`.

**Footer buttons:** current build `pageFooterNextButton` / `pageFooterBackButton`. Older `bottom-navigation-next-button` is **absent from the 2026.38 code**; match both. The Next button's label changes to **"Submit" on the Review page**.

**Field IDs.** Fields sit in `div[data-automation-id="formField-{name}"]`. Input IDs like `name--legalName--firstName`, `address--city`, `phoneNumber--phoneNumber`, `source--source`, `candidateIsPreviousWorker`, `workExperience-{N}--jobTitle`, `education-{N}--schoolName`, `personalInfoUS--gender|ethnicity|veteranStatus`, `termsAndConditions--acceptTermsAndAgreements`. Older sites: `legalNameSection_firstName`, `addressSection_city`. Support both, fall back to the `formField-*` label.

**Dropdowns:** `button[aria-haspopup=listbox]` opens `[role=listbox] [role=option]`.

**Search prompts** (Source, Skills, School): `multiselectInputContainer` / `input[placeholder=Search]` with `[data-automation-id=promptOption]`. "How did you hear about us" is two-level.

**Dates:** separate `dateSectionMonth-input`, `dateSectionDay-input`, `dateSectionYear-input`. The widget re-renders after each keystroke; re-locate before typing. Resume-parsed dates have been seen turning into `12/<year>` on Review.

**Resume upload:** `input[data-automation-id=file-upload-input-ref]` (`setInputFiles`), `file-upload-drop-zone`, `file-upload-successful`. DOC, DOCX, PDF, HTML, TXT, 5 MB max.

**"Autofill with Resume"** (when `includeResumeParsing:true`): parses experience, education, skills. Often creates duplicate or wrong rows; re-uploading doesn't fix a bad parse, delete rows first. Unknown whether it merges or replaces existing rows.

**Self Identify (CC-305):** `selfIdentifiedDisabilityData--name`, a date-signed field, `disabilityStatus`. Fixed choice IDs: Yes `64cbff5f364f10000ae7a421cf210000`, No `64cbff5f364f10000aeec521b4ec0000`, Prefer not to answer `64cbff5f364f10000af3af293a050000`.

### (d) Accounts, verification and drafts

**Accounts:** **one per employer.** Career sites of the same employer share it. NVIDIA warns duplicate profiles "may result in disqualification". **Email verification** is an employer setting; check `approot.featureFlags.accountVerification` (live: true on Workday's own site, false on NVIDIA and Intel). When on, it sends a **verification link**, not a code. Password rules from `account/configuration` (NVIDIA: 8+ chars, upper and lower, number, special).

**Drafts:** saved on Workday's servers; each "Save and Continue" saves that step. Resume path: Candidate Home (`/{site}/userHome`) → Draft Applications → Continue. Lookup via `candidatehome/…/applications?draftsOnly=true`. Resuming from another browser should work after sign-in (untested).

**Session length:** unverified; third parties say 30–60 minutes. Cloudflare `__cf_bm` cookie lasts 30 minutes.

### (e) Bot detection

**No CAPTCHA found** in the front-end code. Instead a **"NoCaptcha" overlay**: transparent `div[data-automation-id=click_filter]` over the real button, which is hidden (`aria-hidden`, `tabindex=-2`). Click `click_filter`, not the hidden button. Seen live on sign-in and create-account; unverified whether it covers final Submit. Cloudflare in front of every employer site; no reports of Playwright being blocked.

**Filling React-controlled fields:** real clicks and typing. If setting values from JS, dispatch `input`, `change` **and `blur`**.

**If Next seems to do nothing:** a required field is empty; look for "Error:" in `[data-automation-id^=formField-]`. Most often missed: `formField-source`, `formField-candidateIsPreviousWorker`.

### (f) Review page and the stop rule

Review is showing when: `applyFlowReviewPage` (or `reviewSubmitPage`) is visible; the active progress step contains "Review"; the footer Next button's text matches `/submit/i`.

Two layers: never click `pageFooterNextButton` / `bottom-navigation-next-button` when its text is Submit; block `page.route('**/wday/cxs/*/jobapplication/*/finalize*', r => r.abort())`.

### (g) Confirmation signals

- `finalize` returns `{id}` and fires analytics `apply_job_application_submitted`.
- On screen: "Congratulations!" or "Your application has successfully been submitted." No distinctive URL.
- Candidate Home moves the job to "Submitted Applications"; reopening shows `alreadyAppliedPage`.
- Emails optional per employer, no standard subject.

---

## 2. Greenhouse

### (a) Reading questions (public, no key)

`GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?questions=true`. Blocks:
- **`questions[]`:** `{label, required, fields:[{name, type, values}]}`. Types: `input_text`, `input_file`, `textarea`, `input_hidden`, `multi_value_single_select`, `multi_value_multi_select`. Standard names `first_name`, `last_name`, `email`, `phone`, `resume`/`resume_text`, `cover_letter`; custom `question_{id}`.
- **`location_questions`:** `location`, plus hidden required `latitude`/`longitude` filled by a geocoder.
- **`compliance`:** US EEOC `gender`, `race`, `hispanic_ethnicity`, `veteran_status`, `disability_status`. Option values are strings, e.g. `"1"`.
- **`demographic_questions`:** `{questions:[{id, label, answer_options:[{id, label, decline_to_answer}]}]}`.
- **`data_compliance`:** GDPR flags.

`absolute_url` often points to the employer's site; build `https://job-boards.greenhouse.io/{token}/jobs/{id}` yourself. No rate limits documented.

### (b) Submit API

`POST boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}` with the **employer's secret key**. Not usable by a candidate tool.

### (c) Form structure

- `boards.greenhouse.io` (incl. `/embed/job_app`) **301-redirects to `job-boards.greenhouse.io`**. Old field names not found live.
- React (Remix), one page: `form#application-form`.
- **Element IDs match API field names:** `#first_name`, `#last_name`, `#email`, `#phone`, `#question_38318888002`; `#resume` / `#cover_letter` `input[type=file]` (pdf/doc/docx/txt/rtf), plus Dropbox, "Enter manually", Google Drive.
- **Dropdowns are react-select comboboxes:** `input.select__input#question_X[role=combobox]`, value stored in a hidden required input. EEOC use `#gender`, `#veteran_status`; demographic use numeric IDs. Focus, type, click `[role=option]` or Enter. Typing without committing clears on blur.
- **Location:** `#candidate-location` autocomplete; must pick a suggestion.
- No resume parsing on the anonymous form.

### (d) Accounts

None needed. MyGreenhouse optional (emailed one-time code). No drafts. **After Submit,** low reCAPTCHA score returns `428 captcha-failed`; page shows 8 code boxes (`#security-input-0..7`) and an email "Security code for your application to <company>" arrives; code only works in the same browser session (third-party code, not docs).

### (e) Bot detection

Invisible **reCAPTCHA Enterprise** loads on page load. Also an email validator, Snowplow, Rollbar.

### (f) Stop rule

Only submit is `#application-form button[type=submit]` ("Submit application"). Don't press Enter inside the form. Optionally add a capturing `submit` listener with `preventDefault()`.

### (g) Confirmation

URL becomes `job-boards.greenhouse.io/{token}/jobs/{id}/confirmation` (also loads directly; confirm by the redirect after click). Employer `confirmation_message` shown. Email subject per employer; "Thank you for applying to {Company}" common.

---

## 3. Lever

### (a) Reading questions

- **Public API: no questions.** `GET https://api.lever.co/v0/postings/{site}?mode=json` returns `id`, `text`, `categories`, `hostedUrl`, `applyUrl`, `workplaceType`. EU: `api.eu.lever.co`.
- **Paid v1 API:** `GET /v1/postings/:id/apply` returns `customQuestions` and `eeoQuestions`; employer key.
- **No-key path:** hidden `cards[{uuid}][baseTemplate]` inputs contain the question definitions as JSON. Field N maps to `cards[uuid][fieldN]`.

### (b) Submit API

`POST /v0/postings/{site}/{id}?key=…` (2/sec) and v1 `POST /v1/postings/:id/apply`. Employer key.

### (c) Form structure

`jobs.lever.co/{co}/{id}/apply`, one server-rendered page, `form#application-form`:
- **Resume:** `#resume-upload-input[name=resume]`.
- **Personal:** `name=name`, `email`, `phone`, `org`.
- **Location:** `#location-input` fills hidden `#selected-location` JSON. Typing alone may not set it.
- **Links:** `urls[LinkedIn]`, `urls[GitHub]`.
- **Custom questions are native elements:** radios, checkboxes, `<select>`, `<textarea>`.
- **EEO:** `select[name="eeo[veteran]"]`, `eeo[disability]` (requires signature and date), sometimes `eeo[gender]`/`eeo[race]`.
- **Resume parsing:** `/parseResume` fills only untouched fields. **Upload first, then set values.**

### (d) Accounts

None, no verification, no drafts.

### (e) Bot detection

**hCaptcha** (`div#h-captcha`), Cloudflare. Report: after a human solved the captcha in an automation-attached browser, Lever returned "There was an error verifying your application" and cleared the file inputs ([JobTomatik #539](https://github.com/TheHighBrid/JobTomatik/issues/539)).

### (f) Stop rule

Visible button `#btn-submit[data-qa=btn-submit]`; runs hCaptcha then clicks hidden `#hcaptchaSubmitBtn`. **Pressing Enter in any text field submits.** Use `fill()`, never a typed `\n`.

### (g) Confirmation

URL becomes `/{co}/{id}/thanks`, "Application submitted!". Email subject unverified.

---

## 4. Ashby

### (a) Reading questions

- **Public posting API, no questions:** `GET https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true`.
- **Site GraphQL, no auth:** `POST https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting`. Query `jobPosting(organizationHostedJobsPageName, jobPostingId){ applicationForm{ sections{ fieldEntries{ … on FormFieldEntry{ field isRequired }}}} surveyForms{…} recaptchaAction }`. Field types: String, Email, Phone, Location, File, LongText, Boolean, Date, ValueSelect, MultiValueSelect (`selectableValues`). System paths `_systemfield_name`, `_email`, `_location`, `_resume`; custom fields by UUID. EEOC in `surveyForms`: `_systemfield_eeoc_gender`, `_race`, `_veteran_status`. Undocumented, may change.

### (b) Submit API

Official `applicationForm.submit` needs employer key with `candidatesWrite`. Web form's call carries `recaptchaToken` and `deviceFingerprint`; not replayable.

### (c) Form structure

`/{org}/{id}/application`, React single page, no `<form>` element. Fields in `div.ashby-application-form-field-entry[data-field-path=…]`:
- **Text:** `#_systemfield_name`, `#_systemfield_email`.
- **Resume:** `input[type=file]#_systemfield_resume`.
- **Location:** combobox, must select a result.
- **Yes/No:** `button.ashby-application-form-input-yesno-option[data-option=yes|no]`. **No `type`, so each is a submit button.** Exact selectors only; never Enter or Space on them.
- **Multi-select:** checkboxes. **EEOC:** radios.
- **"Autofill from resume"** (`.ashby-application-form-autofill-pane`) overwrites fields; run first.

### (d) Accounts

None; no drafts.

### (e) Bot detection

reCAPTCHA v3 (score-based). Front-end can reject a low score (`recaptchaScoreBelowThreshold`).

### (f) Stop rule

Never click `button.ashby-application-form-submit-button`. Block `non-user-graphql?op=ApiSubmitSingleApplicationFormAction` and `op=ApiSubmitMultipleFormsAction`.

### (g) Confirmation

Form replaced in place by `.ashby-application-form-success-container`. URL doesn't change. Email subject per employer.

---

## 5. SmartRecruiters

### (a) Reading questions

- **Posting API, no auth:** `GET https://api.smartrecruiters.com/v1/companies/{co}/postings[/{id}]` (live on Bosch; SAP and Visa returned 0).
- **`/postings/{uuid}/configuration`** has the questions but **needs `X-SmartToken`**.
- **No-key path:** apply page `jobs.smartrecruiters.com/oneclick-ui/company/{co}/publication/{uuid}` puts everything in `window.__OC_CONTEXT__`: `screeningConfiguration.questions[]` (EEO flagged `diversity:true`), feature flags, consent settings.
- **Required standard fields:** `GET /oneclick-ui/api/company/{co}/publication/{uuid}/config`. Trust `/config` over `__OC_CONTEXT__` on resume-required.

### (b) Submit API

`POST /postings/{uuid}/candidates` needs employer key. No drafts.

### (c) Form structure

- Angular app of `spl-*` web components with open shadow roots; Playwright locators reach inside.
- **Two pages:** `…/publication/{uuid}` (Easy Apply with parsing and LinkedIn/Indeed/SEEK, personal details, experience, education, resume, consent), then `/screening`.
- Selectors: `oc-input[data-test=personal-info-first-name-input]`, `…-email-input`, **`…-email-confirm-input`**, `data-test=add-experience`, `data-test=footer-next`. Dropdowns `spl-select` / `spl-select-option`.

### (d) Accounts

None for guest flow.

### (e) Bot detection

**DataDome:** curl got 403 challenge; page has a DataDome interceptor (`data-test=dataDome-dialog`). SEON optional. Playwright Chromium loaded without a challenge.

### (f) Stop rule

With screening questions, final submit is on `/screening`. Without, page 1's primary button is the submit.

### (g) Confirmation

URL moves to `…/success`. Email unverified.

---

## 6. iCIMS

- **APIs:** none for questions. Job Portal API covers postings only.
- **Form:** career page wraps `#icims_content_iframe`. Apply `a.iCIMS_ApplyOnlineButton` → `/jobs/{id}/{slug}/login`. **Email step:** `form#enterEmailForm`, `input#email[name=css_loginName]`, `#accept_gdpr`, `#enterEmailSubmitButton`. Then password, profile with resume upload (parsing re-renders the form), questions, EEO, submit. Tips (unverified): upload resume first; dropdowns are `select` plus `ul.dropdown-results li`, click the `li`; School/Major need real keystrokes.
- **Accounts:** password per employer; "Finish Later" draft.
- **Bot detection:** AWS WAF (`x-amzn-waf-action: captcha` on curl), invisible hCaptcha on the email step. Playwright loaded fine.
- **Stop rule:** final page, before submit. **Confirmation:** page plus dashboard; URL unverified.

## 7. Oracle Cloud HCM (Candidate Experience)

- **Reading (public REST, live on JPMC):** search `/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber=CX_1001,…`; details `recruitingCEJobRequisitionDetails?finder=ById;Id="…",siteNumber=…`; **apply-flow structure** `recruitingCEApplyFlows?finder=findByRequisitionNumber;RequisitionNumber="…"&expand=all` (**needs header `ora-irc-language: en`**), returns `sections` plus `EsignEnabledFlag`, `QuickApplyEnabledFlag`. Question text loads only after PIN.
- **APIs the page uses:** `recruitingCEVerificationTokens`, `recruitingCECandidates`, `recruitingCEJobApplicationDrafts` (server drafts), `recruitingCEJobApplications` (final submit).
- **Form:** Oracle JET. Routes `/job/{id}/apply/email` → `/apply/pin` → `/apply/section/{n}`. **Email step:** `input#primary-email-0`, **hidden trap `input[name=honey-pot]`**, `#legal-disclaimer-checkbox`. **PIN:** `pin-code-1…6`. Some employers add profile import.
- **Accounts:** 6-digit code by email/SMS. Five wrong tries lock out 30 min. **New candidates may get the code only at the END, after Submit.** Verified session ~4 hours; "Keep Me Signed In" trusts the device.
- **Bot detection:** hCaptcha per employer; invisible on JPMC.
- **Stop rule:** Next `.apply-flow-pagination__button`. **Submit `button.apply-flow-pagination__submit-button`. Never click anything with that class.**
- **Confirmation:** thank-you dialog (`apply-flow.thank-you-dialog.confirmed-submission-header`); "unconfirmed" version when PIN still pending. Email "Job Application Confirmation Notification", configurable.

## 8. SAP SuccessFactors

- **APIs:** OData needs employer OAuth. No candidate API.
- **Form:** `/talentcommunity/apply/{reqId}` → `career{N}.successfactors.com/careers?company=…`. Sign-in `form#careerform` (`#username`, `#password`, "Create an account"). Then one long page with "Save" and "Apply".
- **Accounts:** password per employer; emailed one-time code on by default since 2021. "Saved Applications" server drafts.
- **Bot detection:** reCAPTCHA optional; Cloudflare.
- **Stop:** before "Apply". **Confirmation:** email plus "Jobs Applied".
- **Note:** jobs.sap.com now sends applicants to SmartRecruiters.

## 9. Taleo

- **APIs:** job search `POST /careersection/rest/jobboard/searchjobs?lang=en&portal={id}`. No questions API.
- **Form:** JSF: `jobapply.ftl?job=` → privacy → `login.jsf` → `flow.jsf`. IDs like `dialogTemplate-dialogForm-login-name1`, `…-login-guestapply`. Blocks: resume upload with parsing, personal, prescreening, disqualification, diversity, eSignature, Review, Thank You. Selectors: [kensac Taleo.json](https://github.com/kensac/job-scripts/blob/main/extension/adapters/recipes/Taleo.json).
- **Accounts:** per employer; some guest. "Save as Draft" server-side.
- **Bot detection:** none seen. **Many sites return "Career Section Unavailable"**; Taleo is being phased out.
- **Stop:** Review, before eSignature/Submit. **Confirmation:** "Thank You" and "My Submissions".

## 10. Jobvite

- **Reading:** XML feed `jobs.jobvite.com/CompanyJobs/Xml.aspx?c={companyId}`. **Questions embedded in** `GET jobs.jobvite.com/{co}/job/{id}/apply` as `angular.module('preloadedData').constant('preloadedData',{applyFields, eeoFields, ofccpFields, preScreeningFormFields, enableResumeParsing, …})`.
- **Submit:** web form calls `…/submitApplication?g-recaptcha-response=`.
- **Form:** AngularJS, steps main → EEO → OFCCP → prescreen. IDs `#jv-field-{viewFieldEId}`. Dropdowns **native `<select>`**. Resume `#file-input-0`, 10 MB. Some employers show Data Consent first (`body.jv-page-consentform`).
- **Accounts:** none, no drafts. **Bot detection:** invisible reCAPTCHA on "Send Application", Cloudflare.
- **Stop:** intermediate `button[aria-label="Next"]`; **stop when `button[type=submit][aria-label="Send Application"]` is visible.** **Confirmation:** `…/job/{id}/applyConfirmation`.

## 11. Rippling

- **Reading (public):** `GET https://ats.rippling.com/api/v2/board/{slug}/jobs` (dedupe by `id`); `…/jobs/{uuid}` returns `basicQuestions[{oid, fieldType, required}]`, `additionalQuestions[].form.questions[{uniqueKey, dataType, questionType:"KNOCKOUT", strChoices}]`, `eeocQuestionnaireEnabledForJobPost`.
- **Submit:** `POST /api/v1/board/{slug}/jobs/{id}/apply` with Turnstile token in `cf-client-response`.
- **Form:** Next.js, one page `/{slug}/jobs/{id}/apply?step=application`. **Use `data-testid`** (`input-first_name`, `input-email`, `input-resume`, `radio-customQuestions.{formId}.{key}`); **input `name`s are random per load**. Custom comboboxes. Resume parsed to autofill. Voluntary EEO block.
- **Accounts:** none. **Bot detection:** **Cloudflare Turnstile**, Datadog session replay.
- **Stop:** `button[type=submit]` text "Apply". **Confirmation:** "You have successfully applied to {job}".

## 12. Gem

- **Reading (public):** `GET https://api.gem.com/job_board/v0/{board}/job_posts/{id}?questions=true` (Greenhouse-like). GraphQL `oatsJobPostFieldsAndQuestions` at `jobs.gem.com/api/public/graphql/batch`.
- **Submit:** BETA `POST …/applications`, employer key.
- **Form:** one page. **Inputs have no name or id**; locate by label. Radio IDs equal option `extId`s. Dropdowns custom buttons "Please select". Voluntary self-ID section.
- **Accounts:** none. **Bot detection:** **hCaptcha, FingerprintJS Pro, "AI Fraud Detection Agent"** flagging "inhuman speed" and many applications per IP. Fill at human pace.
- **Stop:** before **both** "Apply and save" and "Apply without saving". **Confirmation:** "Congratulations! Your application for {job} has been received!" in place. Default email "Thank you for applying to {company}".

## 13. Eightfold

- **Reading (two generations):** **Legacy** `/api/apply/v2/jobs?domain=…` (`apply_redirect_url` often hands off to another ATS). **PCSX** `/api/pcsx/position_details?position_id=…&domain=…`; `positionUserActions.applyAction.status`: `allowed` (guest), `log_in`, `link_off` (outside `applyUrl`, e.g. Workday). **Questions** `POST /api/application/v2/questions?…&user_mode=logged_out_candidate`, needs `x-csrf-token` from the page. IDs like `QUESTION_SETUP-…` suggest sync from Workday.
- **Submit:** `/api/application/v2/submit`.
- **Form:** one long page (`/careers/apply?pid=`). IDs `#Contact_Information_firstname`. Custom comboboxes. EEO, VEVRAA, CC-305 inline.
- **Accounts:** vary; email accounts get a verification link. **Bot detection:** invisible reCAPTCHA, CSRF.
- **Stop:** `button[data-test-id="submitApplicationButton"]`. **Confirmation:** in-page modal (`#form-submission-success-message`, approximate), link to `/careers/applications`.

## 14. Phenom (custom career sites)

- **Detect** by `phApp.ddo` in HTML.
- **Reading:** `POST https://{host}/widgets` with `{"ddoKey":"refineSearch","refNum":…}`; `ddoKey:"jobDetail"` returns `ats` and `applyUrl`. Many sites link to Workday (Mastercard, eBay; 68 of 91 in one study). For Phenom-hosted forms, schema is public: `cdn-prod-static.phenompeople.com/CareerConnectResources/{refNum}/workday/external/careersite/singlepage/schema/form_schema.json`.
- **Submit:** posts to `/applySubmit`.
- **Form:** steps My information → My experience → Questions → Additional → Voluntary Disclosures → Review. Field names mirror Workday (`cntryFields.firstName`, `phoneWidget.countryPhoneCode`).
- **Drafts:** **`submitOnEachStep:true`, every "Next" saves to the server.** Sessions expire.
- **Bot detection:** reCAPTCHA configurable, Microsoft Clarity.
- **Stop:** Review, before "Submit"; intermediate `button#next`. **Confirmation:** `/{locale}/applythankyou?jobSeqNo=…`.

## 15. LinkedIn Easy Apply

**Terms.** User Agreement §8.2 (effective Nov 3, 2025) prohibits "software, devices, scripts, robots… (such as crawlers, browser plugins and add-ons)" and "bots or other unauthorized automated methods". Accounts can be restricted or terminated.

**Enforcement:** Easy Apply daily cap and pauses fast submitters; widely reported ~50/day. April 2026 reporting found LinkedIn checking for over 6,000 Chrome extension IDs ([BleepingComputer](https://www.bleepingcomputer.com/news/security/linkedin-secretly-scans-for-6-000-plus-chrome-extensions-collects-data/)). LinkedIn v. ProAPIs ended in a consent judgment 2026-09-16.

**Recommendation:** don't automate inside LinkedIn at all, even filling only. At most detect a job's outside "Apply" link and hand off to the employer's ATS.

---

## The most important gotchas for a filler that must stop before submit

1. **Stop rules need two layers: never click the button, and block the submit call.**

   | ATS | Block this request |
   |---|---|
   | Workday | `**/jobapplication/*/finalize*` |
   | Ashby | `non-user-graphql?op=ApiSubmit*` |
   | Greenhouse | the apply POST |
   | Jobvite | `*/submitApplication*` |
   | Rippling | `*/jobs/*/apply` |
   | Gem | `/api/ats/job_posts/*/apply` |
   | Eightfold | `/api/application/v2/submit` |
   | Phenom | `/applySubmit` |

   Also add a capturing `submit` listener that calls `preventDefault()`.
2. **Accidental submits:** Lever, Enter in any text field submits. Ashby, Yes/No buttons have no `type`. Workday, Next *becomes* Submit on Review. SmartRecruiters, page 1's button is Submit when there are no screening questions. Gem, two submit buttons. Always check the button's label and current page before any click.
3. **Hidden trap fields:** Workday `beecatcher[name=website]`, Oracle `honey-pot`. Skip `aria-hidden` and off-screen inputs.
4. **"Next" already saves on some platforms.** Workday, Phenom, Oracle drafts, iCIMS save each step server-side. A half-filled application exists as a draft on the employer's side: useful for resuming, and visible to the employer.
5. **Workday accounts are one per employer**, sometimes with email verification (check `approot.featureFlags.accountVerification`). Duplicate profiles can disqualify. Needs a credential vault keyed by employer; let the human handle verification links.
6. **Verification codes can come AFTER the human clicks Submit:** Greenhouse 8-character security code on low reCAPTCHA score, Oracle PIN for new candidates. Don't mark "applied" until entered; code only works in the same browser session.
7. **Run resume autofill first, then overwrite.** Lever, Ashby, Workday, iCIMS, SmartRecruiters, Rippling parse the resume and overwrite or re-render. Workday parsing creates duplicate rows and wrong dates; re-check on Review.
8. **Custom dropdowns must be committed.** react-select (Greenhouse), Workday listboxes, `spl-select`, Eightfold, Rippling, Gem: type, then click `[role=option]`. Location/geocode fields (Greenhouse, Lever, Ashby, Rippling) need a suggestion picked.
9. **Use real events, not injected values.** Prefer Playwright `fill`/`type`/`click`. If setting from JS, dispatch `input`, `change`, `blur`. Workday's overlay needs a click on `click_filter`.
10. **Hand the final click to a normal browser.** CAPTCHAs run at submit (hCaptcha on Lever/iCIMS/Gem, reCAPTCHA on Greenhouse/Ashby/Jobvite/Eightfold, Turnstile on Rippling, DataDome on SmartRecruiters). Lever has rejected a human-solved captcha in an automation-attached browser. Prefer filling in the user's real browser or detaching automation before the human clicks Submit. Pace inputs like a human for Gem and Rippling.
11. **Many career sites front another ATS.** Phenom `jobDetail.ats`, Eightfold `applyAction.status`/`apply_redirect_url`, Greenhouse `absolute_url`, jobs.sap.com → SmartRecruiters.
12. **Confirmation signals:** reliable URL/page: Greenhouse `/confirmation`, Lever `/thanks`, Jobvite `/applyConfirmation`, SmartRecruiters `/success`, Phenom `/applythankyou`, Oracle dialog, Workday "Congratulations!" plus `alreadyAppliedPage`. In place, no URL change: Ashby, Gem, Rippling, Eightfold. Emails unreliable (per-employer subjects; Workday often sends none). Best signal: the platform's own submit call succeeding while the human clicks. Several confirmation URLs load by direct navigation, so URL alone doesn't prove submission.

**Still unverified:** confirmation email subjects (except Gem's default), Workday session length and cross-browser resume, whether Workday's click-overlay covers final Submit, iCIMS confirmation URL, Rippling developer docs.

Raw captures (JSON and HTML) and Workday's downloaded front-end code are in the session scratchpad: `gh_job.json`, `lever_apply_us.html`, `ashby_gql.json`, `phschema.json`, `efcap.json`, `ripj.json`, plus the inspector script `insp.py`.
