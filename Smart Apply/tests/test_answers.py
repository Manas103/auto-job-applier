"""The deterministic answer layer against question shapes seen across 12 Greenhouse boards, 2 Lever and 2 Ashby
postings on 2026-09-24. Every rule here is one Claude call (or one wrong guess) avoided."""

from __future__ import annotations

from smartapply.answers import KNOCKOUT, deterministic_answers, gpa_option, grad_option
from smartapply.models import Question
from smartapply.profile import Profile, seed_from_assets


def _p() -> Profile:
    p = seed_from_assets(None)
    p.address.city, p.address.state, p.address.state_name = "Springfield", "IL", "Illinois"
    return p


def test_gpa_and_graduation_matchers():
    assert gpa_option(['Not Applicable', '4.0 out of 4.0', '3.9 out of 4.0', '3.8 out of 4.0', '3.7 out of 4.0', '3.6 out of 4.0'], 3.70) == '3.7 out of 4.0'
    assert gpa_option(['Below 2', '2 - 2.24', '3.5 - 3.74', '3.75 - 4'], 3.70) == '3.5 - 3.74'
    assert gpa_option(['Below 3', '3.0+'], 3.70) == '3.0+'
    assert gpa_option(['Below 2', '2 - 2.24'], 3.70) is None
    assert grad_option(['December 2026', 'May 2027', 'May 2028'], "2028-05") == 'May 2028'
    assert grad_option(['01/2028', '05/2028', '06/2028'], "2028-05") == '05/2028'
    assert grad_option(['Spring 2027', 'Fall 2027', 'Spring 2028', 'Fall 2028'], "2028-05") == 'Spring 2028'
    assert grad_option(["I've already graduated", 'January 2028 - July 2028', 'August 2028 - December 2028'], "2028-05") == 'January 2028 - July 2028'
    assert grad_option(['2027', '2028', '2029'], "2028-05") == '2028'
    assert grad_option(['2026', '2027'], "2028-05") is None


def test_label_rules_from_the_board_survey():
    p = _p()
    qs = [
        Question("q1", "Which college or university do you currently attend?", "select", True, ["Brown University", "State University", "Other (School Not Listed)"]),
        Question("q2", "Please re-confirm the university you currently attend", "select", True, ["MIT", "Harvard University", "Other"]),
        Question("q3", "Undergrad Discipline(s)", "multiselect", True, ["Mathematics", "Computer Science", "Physics"]),
        Question("q4", "What country do you currently reside in?", "select", True, ["Canada", "United States", "Canada"]),
        Question("q5", "If applicable, which US state do you reside in?", "select", False, ["Alabama", "Illinois", "Georgia"]),
        Question("q6", "How did you hear about DV Trading?", "select", True, ["LinkedIn", "DV Website", "Other"]),
        Question("q7", "How did you hear about this opportunity?", "select", True, ["Campus", "Company Website", "Job Board"]),
        Question("q8", "Citizenship Status", "select", True, ["1) U.S. citizen or national", "2) U.S. lawful permanent resident", "6) Other"]),
        Question("q9", "Active Security Clearance(s)", "multiselect", True, ["Top Secret", "Secret", "None"]),
        Question("q10", "Are you willing to undergo a background check, in accordance with local law?", "select", True, ["Yes", "No"]),
        Question("q11", "Have you previously interviewed at Rocket Lab?", "select", True, ["Yes", "No"]),
        Question("q12", "How many prior internship(s) and/or co-op(s) have you completed in software engineering?", "select", True, ["None", "1", "2", "3+"]),
        Question("q13", "Do you currently have legal work authorization in the country where this position is located?", "select", True, ["Yes", "No"]),
        Question("q14", "Would you, now or in the future, require immigration sponsorship for work authorization?", "select", True, ["Yes", "No"]),
        Question("q15", "Are you enrolled in a Bachelor's, Master's or PhD program from an accredited college or university?", "select", True, ["Yes", "No"]),
        Question("q16", "Undergraduate GPA: Please convert your GPA to a 4.0 scale.", "select", True, ["Not Applicable", "3.7 out of 4.0", "3.6 out of 4.0"]),
        Question("q17", "Graduate GPA: Please convert your GPA to a 4.0 scale.", "select", True, ["Not Applicable", "3.7 out of 4.0"]),
        Question("q18", "Select your anticipated master's degree graduation date", "select", True, ["Not Applicable", "05/2028"]),
        Question("q19", "What is your anticipated graduation month/year?", "select", True, ["May 2027", "December 2027", "May 2028"]),
        Question("q20", "Terms & Conditions", "select", True, ["Yes, I have read and agree to DV Trading's Privacy Policy."]),
        Question("q21", "What are your preferred pronouns?", "select", True, ["She/Her", "He/Him", "They/Them", "Prefer not to say"]),
        Question("q22", "Check Yes or No to indicate your agreement to receive text message updates.", "select", False, ["Yes", "No"]),
        Question("q23", "Are you willing to work onsite?", "select", True, ["Yes", "No"]),
        Question("q24", "The person hired will have access to ITAR-controlled information. Please identify which statement applies to you:", "select", True,
                 ["A United States citizen or national", "A person lawfully admitted for permanent residence of the United States", "None of the Above"]),
        Question("q25", "Phone Number", "text", True),
        Question("q26", "Current company", "text", True),
    ]
    a = deterministic_answers(qs, p, {})
    assert a["q1"].value == "State University"
    assert a["q2"].value == "Other"                       # SU not listed: the honest option, not a guess
    assert a["q3"].value == ["Computer Science"]
    assert a["q4"].value == "United States" and a["q5"].value == "Illinois"
    assert a["q6"].value == "DV Website" and a["q7"].value == "Campus"   # an event or campus beats a job board (2026-10-11)
    assert a["q8"].value == "2) U.S. lawful permanent resident"
    assert a["q9"].value == ["None"]
    assert a["q10"].value == "Yes" and a["q11"].value == "No"
    assert a["q12"].value == "1"                          # one internship in the profile (Acme Labs)
    assert a["q13"].value == "Yes" and a["q14"].value == "No" and a["q15"].value == "Yes"
    assert a["q16"].value == "3.7 out of 4.0" and a["q17"].value == "Not Applicable"
    assert a["q18"].value == "Not Applicable" and a["q19"].value == "May 2028"
    assert a["q20"].value.startswith("Yes, I have read")
    assert a["q21"].needs_human                            # required pronouns: Alex's, never inferred
    assert a["q22"].value == "Yes"
    assert "q23" not in a and KNOCKOUT.search(qs[22].label)   # on-site willingness stays a knock-out
    assert a["q24"].value.startswith("A person lawfully admitted")
    assert a["q25"].value == "5550100142" and a["q26"].value == "State University"


def test_text_consent_maps_to_opt_in_options():
    # Walmart (2026-10-01) phrases the texting consent as Opt-In / Opt-Out options instead of Yes / No
    p = _p()
    q = Question("t1", "Would you like to receive mobile text message updates relating to your employment relationship with Walmart?",
                 "select", True, ["Opt-In to receive text messages from Walmart", "Opt-Out from receiving text messages from Walmart"])
    out = deterministic_answers([q], p, {})
    assert out["t1"].source == "profile" and out["t1"].value.startswith("Opt-In" if str(p.defaults.text_consent).lower() == "yes" else "Opt-Out")


def test_intended_graduation_year_is_the_graduation_date():
    p = _p()
    q = Question("g1", "Please include your intended graduation year for the degree or relevant learning program that you are "
                 "currently pursuing or have completed.", "select", True, ["2026", "2027", "2028", "2029", "Other"])
    out = deterministic_answers([q], p, {})
    assert out["g1"].value == p.school.graduation[:4]


def test_sponsorship_mentioning_spouse_is_not_a_relatives_question():
    p = _p()
    q = Question("s1", "Will you now or could you in the future require sponsorship to obtain work authorization or to transfer or "
                 "extend your current visa? (You must answer Yes if your current visa is tied to your spouse or partner's visa "
                 "or if you are on a bridging visa or working holiday visa)", "select", True, ["Yes", "No"])
    out = deterministic_answers([q], p, {})
    assert out["s1"].value == ("Yes" if p.work_auth.sponsorship_required else "No") and not out["s1"].needs_human


def test_export_control_fine_print_is_not_a_relatives_question():
    # Intel, 2026-10-13: "family based immigration" and "U.S. Government" in the note matched the relatives rule ("No")
    p = _p()
    p.work_auth.status, p.work_auth.citizen, p.work_auth.itar_us_person = "U.S. permanent resident (green card)", False, True
    p.attestations.relatives_in_government = "No"
    q = Question("e1", "9)* Export Control: Are you a U.S. citizen or national, lawful permanent resident, or have been approved "
                 "for refugee or asylee status by the U.S. Government? (Authorization from the U.S. Government may be required "
                 "before employment may begin. NOTE: Select “No” if you have a family based immigration, DACA, TPS or PIP.",
                 "select", True, ["Yes", "No"])
    assert deterministic_answers([q], p, {})["e1"].value == "Yes"


def test_family_at_employer_no_but_friends_stay_open():
    p = _p()
    p.attestations.relatives_at_employer = "No"
    fam = Question("f1", "Do you have an immediate family member who is currently an employee of Salesforce?", "select", True, ["Yes", "No"])
    both = Question("f2", "Do you have any relatives or individuals you share a close personal relationship with employed by NiSource?",
                    "select", True, ["Yes", "No"])
    gov = Question("f3", "Do you have a family member who is a government official?", "select", True, ["Yes", "No"])
    out = deterministic_answers([fam, both, gov], p, {})
    assert out["f1"].value == "No" and not out["f1"].needs_human
    assert out["f2"].needs_human and "friends_at_employer" in out["f2"].needs_human
    assert out["f3"].needs_human
    p.attestations.friends_at_employer = "No"
    assert deterministic_answers([both], p, {})["f2"].value == "No"


def test_stated_eeo_answers_match_common_wordings():
    p = _p()
    p.eeo.veteran, p.eeo.disability, p.eeo.ethnicity = "I am not a protected veteran", "No, I do not have a disability", "Asian"
    qs = [Question("v1", "Veteran status", "select", True, ["I am not a veteran", "I am a veteran", "Decline to self-identify"]),
          Question("v2", "Are you a protected veteran?", "select", True, ["Yes", "No", "I don't wish to answer"]),
          Question("d1", "Disability status", "select", True, ["Yes, I have a disability", "No, I don't have a disability", "I don't wish to answer"]),
          Question("d2", "Do you have a disability?", "select", True, ["Yes", "No", "Decline"]),
          Question("r1", "Race / ethnicity", "select", True, ["White (Not Hispanic or Latino)", "Asian (Not Hispanic or Latino)", "Decline To Self Identify"])]
    out = deterministic_answers(qs, p, {})
    assert out["v1"].value == "I am not a veteran" and out["v2"].value == "No"
    assert out["d1"].value == "No, I don't have a disability" and out["d2"].value == "No"
    assert out["r1"].value == "Asian (Not Hispanic or Latino)"


def _stated(p):
    d = p.defaults
    d.summer_start, d.driver_license, d.essential_functions, d.travel, d.onsite = "2027-05-01", "Yes", "Yes", "Yes", "Yes"
    d.commute_city, d.pay_range_ok, d.salary_text, d.full_time_availability, d.offer_deadlines = "Springfield", "Yes", "Open / negotiable", "Yes", "No"
    p.school.high_school_name, p.school.high_school_graduation = "Example High School", "2024-05"
    for k in ("government_employment_5y", "relatives_in_government", "friends_at_employer", "conflict_of_interest",
              "criminal_conviction", "debarred", "non_compete_bound", "other_employment", "relatives_at_employer"):
        setattr(p.attestations, k, "No")
    return p


def test_logistics_answers_from_the_profile():
    p = _stated(_p())
    yn = ["Yes", "No"]
    summer = {"title": "Software Engineer Intern - Summer 2027", "locations": '["Boston, MA"]', "pay": ""}
    qs = [
        Question("a", "Are you currently in possession of a valid driver's license?", "select", True, yn),
        Question("b", "Are you capable of performing, with or without reasonable accommodation, the activities involved in the job?", "select", True, yn),
        Question("c", "Are you willing to travel?", "select", True, yn),
        Question("d2", "Are you willing to travel? What amount of time?", "select", True, ["Up to 10%", "Up to 25%", "Up to 50%"]),
        Question("e", "This role is located out of our Boston headquarters. Are you willing and able to work out of our Boston office 5 days per week?", "select", True, yn),
        Question("f", "This position is located in Westminster, CO. Are you located within a commutable distance of this location?", "select", True, yn),
        Question("g", "This internship pays $30-$31/hour. Are you comfortable with that range?", "select", True, yn),
        Question("h", "What is your desired salary or hourly rate?", "text", True),
        Question("i", "Are you available for a full-time role (40 hours per week) for the entire duration of the dates listed?", "select", True, yn),
        Question("j", "Do you have any, or anticipate any upcoming offer deadlines?", "select", True, yn),
        Question("k", "If selected, when would you be able to start?", "select", True, ["April 2027", "May 2027", "June 2027"]),
        Question("l", "What is your desired start date?", "text", True),
        Question("m", "High School Name", "text", True),
        Question("n", "Year of High School Graduation", "select", True, ["2023", "2024", "2025"]),
        Question("o", "Do you have any agreements or contracts with a current or former employer that place any restrictions on your employment (non-compete)?", "select", True, yn),
        Question("p", "I attest/confirm that I have no post-government employment restrictions currently applicable to me", "select", True, yn),
        Question("q", "Are you currently or have you in the past been debarred, suspended or declared ineligible?", "select", True, yn),
    ]
    out = deterministic_answers(qs, p, {}, summer)
    got = {k: (a.value, a.needs_human) for k, a in out.items()}
    assert got["a"] == ("Yes", None) and got["b"] == ("Yes", None) and got["c"] == ("Yes", None)
    assert got["d2"][0] == "Up to 50%" and got["e"][0] == "Yes" and got["f"][0] == "No" and got["g"][0] == "Yes"
    assert "h" not in got and got["i"][0] == "Yes"   # no posted pay: Claude estimates (build_packet) and got["j"][0] == "No"
    assert got["k"][0] == "May 2027" and got["l"][0] == "May 1, 2027"
    assert got["m"][0] == "Example High School" and got["n"][0] == "2024"
    assert got["o"][0] == "No" and got["p"][0] == "Yes" and got["q"][0] == "No"
    # commute in Springfield, posted pay as the salary answer, co-op start = earliest offered option
    orl = {"title": "Software Intern - Fall 2027 Co-op", "locations": '["Springfield, IL"]', "pay": "$28-$32/hr"}
    out2 = deterministic_answers([qs[5], qs[7], Question("k2", "When would you be available to start?", "select", True,
                                                          ["September 2027", "August 2027", "January 2028"])], p, {}, orl)
    assert out2["f"].value == "No"          # the question names Westminster, CO, not Springfield
    assert out2["h"].value == "In line with the posted range ($28-$32/hr)" and out2["k2"].value == "August 2027"
    near = Question("f3", "Are you located within a commutable distance of our office?", "select", True, yn)
    assert deterministic_answers([near], p, {}, orl)["f3"].value == "Yes"


def test_embargoed_country_question_answered_by_permanent_residence():
    p = _p()
    q = Question("x1", "Are you a citizen, national or permanent resident of a country that is NOT Iran, Cuba, Syria, or North Korea?",
                 "select", True, ["Yes", "No"])
    assert deterministic_answers([q], p, {})["x1"].value == "Yes"
    plain = Question("x2", "Are you a citizen, national or permanent resident of Iran, Cuba, North Korea or Syria? As a U.S. company "
                     "that exports software we must comply with U.S. export control laws.", "select", True, ["Yes", "No"])
    out = deterministic_answers([plain], p, {})["x2"]
    assert out.value is None and out.needs_human          # his citizenship is not in the profile: never answered


def test_how_heard_takes_best_non_referral_option():
    # 2026-10-11, Alex: not "Job Board" or "Other" by default; the best listed option that is not a referral
    p = _p()
    q = Question("h1", "How did you hear about us?", "select", True, ["Referral", "LinkedIn", "Job Board", "Company Website", "Other"])
    assert deterministic_answers([q], p, {}, {"sources": '["simplify"]'})["h1"].value == "Company Website"
    q2 = Question("h2", "How did you hear about this opportunity?", "select", True, ["Employee Referral", "Internet", "Career Fair"])
    assert deterministic_answers([q2], p, {}, {"sources": '["speedyapply"]'})["h2"].value == "Career Fair"
    q3 = Question("h3", "How did you hear about us?", "select", True, ["Employee Referral", "Recruiter", "Other"])
    assert deterministic_answers([q3], p, {}, {"sources": '["simplify"]'})["h3"].value == "Other"
    from smartapply.answers import best_how_heard
    assert best_how_heard(["Select One", "Friend/Family", "Glassdoor", "University Event"]) == "University Event"
    assert best_how_heard(["Referral", "Other"]) is None
    # 2026-10-13: named organizations are membership claims, even when "College" or "Event" is in the name
    assert best_how_heard(["Thurgood Mashall College Fund (TMCF)", "National Society of Black Engineers (NSBE)",
                           "10,000 Black Interns", "Wall Street Bound", "Handshake"]) == "Handshake"


def test_text_messages_opt_out_when_consent_is_no():
    p = _p()
    p.defaults.text_consent = "No"
    q = Question("t2", "Would you like to receive mobile text message updates?", "select", True,
                 ["Opt-In to receive text messages", "Opt-Out from receiving text messages"])
    assert deterministic_answers([q], p, {})["t2"].value.startswith("Opt-Out")


def test_military_is_not_itar():
    p = _p()
    p.eeo.veteran = "I am not a protected veteran"
    q = Question("m1", "Do you have previous military experience?", "select", True, ["Yes", "No"])
    assert deterministic_answers([q], p, {})["m1"].value == "No"
    p.eeo.veteran = "Decline to self-identify"
    out = deterministic_answers([q], p, {}).get("m1")
    assert out is None or out.value != "Yes"        # never "Yes" from the ITAR rule


def test_travel_follow_ups_read_with_the_travel_question():
    p = _stated(_p())
    qs = [Question("t1", "Are you willing to travel?", "select", True, ["Yes", "No", "Maybe"]),
          Question("t2", "What amount of time?", "select", True, ["0%", "25%", "50%", "75%", "100%"]),
          Question("t3", "Where for short term?", "textarea", True),
          Question("t4", "Where for long term?", "textarea", True)]
    out = deterministic_answers(qs, p, {})
    assert out["t1"].value == "Yes" and out["t2"].value == "100%"
    assert out["t3"].value == "Anywhere" and out["t4"].value == "Anywhere"


def test_immigration_case_is_a_sponsorship_question():
    p = _p()
    q = Question("i1", "Will you now or in the future require Tokyo Electron to commence an immigration case in order to employ you?",
                 "select", True, ["Yes", "No"])
    a = deterministic_answers([q], p, {})["i1"]
    assert a.source == "profile" and a.value == ("Yes" if p.work_auth.sponsorship_required else "No")


def test_gender_matches_man_and_male_wordings():
    from smartapply.answers import match_option
    assert match_option(["Man", "Woman", "Non-binary", "I prefer not to disclose"], "Male") == "Man"
    assert match_option(["Male", "Female", "Decline"], "Male") == "Male"
    assert match_option(["Cisgender man", "Cisgender woman", "Prefer not to say"], "Male") == "Cisgender man"
    assert match_option(["Man", "Woman"], "Female") == "Woman"
    p = _p()
    p.eeo.gender = "Male"
    q = Question("g1", "Please select your gender.", "select", True, ["Man", "Woman", "Non-binary", "I prefer not to disclose"])
    assert deterministic_answers([q], p, {})["g1"].value == "Man"


def test_previous_employee_rule_never_answers_an_essay():
    p = _p()
    essay = Question("e1", "Please share your previous internship, co-curricular, research and/or hands-on engineering experience:", "textarea", True)
    yn = Question("e2", "Have you previously worked for Acme?", "select", True, ["Yes", "No"])
    out = deterministic_answers([essay, yn], p, {})
    assert "e1" not in out or out["e1"].value in (None, "")
    assert out["e2"].value == "No"


def test_demographic_pronouns_from_profile():
    p = _p()
    p.pronouns = "He/Him"
    q = Question("pr", "Pronoun", "select", False, ["He/Him/His", "She/Her/Hers", "They/Them/Theirs", "Other", "I don't wish to answer"],
                 section="demographic")
    assert deterministic_answers([q], p, {})["pr"].value == "He/Him/His"


def test_skills_question_with_comfortable_is_not_a_knockout():
    from smartapply.answers import KNOCKOUT
    assert not KNOCKOUT.search("What are some AI specific technologies you are comfortable with?")
    assert KNOCKOUT.search("This internship pays $30-$31/hour. Are you comfortable with that range?")
    assert KNOCKOUT.search("Are you comfortable with the in-office requirement?")


def test_facts_stated_2026_10_05(tmp_path):
    p = _p()
    p.school.sat, p.school.act = "1420", "did not take"
    p.work_auth.citizenship = ["Canada"]
    p.native_name = "Alex Doe"
    p.defaults.relocation_cities = ["New York", "San Francisco", "Chicago"]
    tr = tmp_path / "t.pdf"
    tr.write_bytes(b"%PDF-1.4")
    p.transcript_path = str(tr)
    yn = ["Yes", "No"]
    hp = ("In order to comply with the U.S. Export Control regulations, HP must conduct specific, pre-interview screenings. "
          "Please indicate if you are a citizen or permanent resident of ONE OR MORE of the countries listed below, by answering "
          "'yes'. If you are not a citizen/permanent resident of one of the countries listed below, please answer 'no'. The "
          "countries are: Armenia, Belarus, China (People's Republic of China), Cuba, Iran, North Korea, Russia, Syria, Venezuela.")
    qs = [Question("hp", hp, "select", True, yn),
          Question("listed", "Are you a citizen of any of the following: Iran, Cuba, Canada, Syria?", "select", True, yn),
          Question("inv", "Please confirm you are not a citizen of Iran, Cuba, Syria or North Korea.", "select", True, yn),
          Question("sat", "SAT Score", "text", True), Question("act", "ACT Score", "text", True),
          Question("satr", "SAT score (if taken)", "select", True, ["Below 1400", "1400-1490", "1500-1600", "Did not take"]),
          Question("actr", "ACT composite score", "select", True, ["30+", "Below 30", "I did not take the ACT"]),
          Question("native", "Full Legal Name in Native Language (e.g. Chinese Characters, Cyrillic, Farsi etc)", "text", True),
          Question("reloc", "Please indicate all locations that you would be interested in relocating to for this position.",
                   "multiselect", True, ["New York, NY", "San Francisco, CA", "Toronto, CAN (Winter Only)"]),
          Question("pref", "Which office location do you prefer?", "select", True, ["Austin, TX", "Chicago, IL", "London, UK", "New York, NY"]),
          Question("tr", "Please upload a copy of your transcript (unofficial is fine).", "file", True),
          Question("skills", "What are some AI specific technologies you are comfortable with?", "text", True)]
    out = deterministic_answers(qs, p, {})
    assert out["hp"].value == "No"
    assert out["listed"].needs_human and out["inv"].needs_human        # Canada listed; inverted wording
    assert out["sat"].value == "1420" and out["act"].value == "Did not take"
    assert out["satr"].value == "1400-1490" and out["actr"].value == "I did not take the ACT"
    assert out["native"].value == "Alex Doe"
    assert out["reloc"].value == ["New York, NY", "San Francisco, CA"]
    assert out["pref"].value == "New York, NY"
    assert out["tr"].value == "__transcript__"
    assert "skills" not in out                                          # Claude answers it from the resume


def test_hp_availability_wordings():
    p = _p()
    p.defaults.availability, p.defaults.full_time_availability = "Available May to August 2027, full 12 weeks", "Yes"
    qs = [Question("m", "How many months are you available for this internship?", "textarea", True),
          Question("a", "What is your availability for your HP assignment?", "select", True, ["Full-time", "Part-time", "Full-time OR Part-time"])]
    out = deterministic_answers(qs, p, {}, job={"title": "AI Applied Engineering Intern", "locations": [], "pay": ""})
    assert out["m"].value == "3 months (12 weeks), May to August 2027"
    assert out["a"].value == "Full-time"


def test_based_in_location_is_never_claimed_falsely():
    """Harvey, 2026-10-05: the on-site rule picked "Yes, I'm based in this location" for a San Francisco office."""
    p = _p()
    p.defaults.commute_city, p.defaults.onsite, p.defaults.relocation = "Springfield", "Yes", "Yes"
    opts = ["Yes, I’m based in this location and able to work from the office 3 days per week",
            "No, I’m not based in this location but willing to relocate", "No, I’m only able to work remotely"]
    q = Question("o", "This role is tied to the office location listed in the job posting. Team members are expected to work "
                      "from the office 3 days per week as part of Harvey's hybrid work model. Are you currently based in this location?",
                 "select", True, opts)
    sf = {"title": "Software Engineering Intern", "locations": ["San Francisco, CA"], "pay": ""}
    assert deterministic_answers([q], p, {}, job=sf)["o"].value == opts[1]
    orl = {"title": "Software Engineering Intern", "locations": ["Springfield, IL"], "pay": ""}
    assert deterministic_answers([q], p, {}, job=orl)["o"].value == opts[0]


def test_hp_review_page_corrections():
    """HP review page, 2026-10-05: four wrong answers."""
    from smartapply.answers import match_option
    vet = ["I AM NOT A VETERAN", "I IDENTIFY AS A VETERAN, JUST NOT A PROTECTED VETERAN",
           "I IDENTIFY AS ONE OR MORE OF THE CLASSIFICATIONS OF PROTECTED VETERANS", "I DON'T WISH TO ANSWER"]
    assert match_option(vet, "I am not a protected veteran") == "I AM NOT A VETERAN"
    assert match_option(vet[1:], "I am not a protected veteran") != vet[1]          # never the veteran claim
    assert match_option(["I am not a protected veteran", "I identify as a protected veteran"], "I am not a protected veteran") \
        == "I am not a protected veteran"
    assert match_option(["Applied Computer Science", "Computer Science and Engineering", "Computer Science"], "Computer Science") \
        == "Computer Science"
    assert match_option(["Applied Computer Science", "Computer Science and Engineering"], "Computer Science") \
        == "Applied Computer Science"     # closest remaining; the exact-only first pass in workday.py waits for full results
    p = _p()
    qs = [Question("emp", "To help us understand your preferences related to personal data and uses for future recruitment "
                          "processes, are you an existing HP employee?", "select", True, ["Yes", "No"]),
          Question("np", "What is the notice period you need to give your current employer?", "textarea", False)]
    out = deterministic_answers(qs, p, {})
    assert out["emp"].value == "No"
    assert out["np"].value.startswith("None")


def test_located_in_us_is_the_country_not_the_commute_city():
    p = _p()
    p.defaults.commute_city = "Springfield"
    out = deterministic_answers([Question("us", "Are you located in US?", "select", True, ["Yes", "No"])], p, {})
    assert out["us"].value == "Yes"


def test_stripe_and_pinterest_2026_10_06():
    p = _p()
    p.address.country = "United States of America"
    p.defaults.availability = "Available May to August 2027, full 12 weeks"
    p.defaults.summer_start = "2027-05-01"
    p.defaults.relocation_cities = ["New York", "San Francisco", "Chicago"]
    yn = ["Yes", "No"]
    qs = [Question("coh", "We host cohorts of 12 or 16 week internships over Winter or Summer. Please choose which cohort "
                          "works best for you.", "multiselect", True, ["Winter (January - April)", "Summer (May - September)"]),
          Question("coh2", "Do you have flexibility to consider a second cohort option?", "select", True,
                   ["I am not pursuing another cohort at this time", "Winter (January - April)", "Summer (May - September)"]),
          Question("city", "If located in the US, in what city and state do you reside?", "text", True),
          Question("ctry", "Please select the country where you currently reside.", "select", True, ["Canada", "UK", "US", "Other"]),
          Question("win", "The start date for this role falls in 2027. Are you able to start between January 2027 and "
                          "September 2027?", "select", True, yn),
          Question("late", "Are you able to start between September 2027 and December 2027?", "select", True, yn),
          Question("off", "What is your preferred office location?", "select", True, ["San Francisco", "Palo Alto", "Seattle", "Remote"])]
    out = deterministic_answers(qs, p, {}, job={"title": "Software Engineer Intern - Summer or Winter", "locations": [], "pay": ""})
    assert out["coh"].value == ["Summer (May - September)"]
    assert out["coh2"].value == "I am not pursuing another cohort at this time"
    assert out["city"].value == "Springfield, IL"
    assert out["ctry"].value == "US"
    assert out["win"].value == "Yes" and out["late"].value == "No"
    assert out["off"].value == "San Francisco"


def test_batch_gaps_2026_10_06():
    p = _p()
    p.work_auth.sponsorship_required = False
    p.defaults.languages = ["English"]
    p.school.sat, p.school.act = "1420", "did not take"
    qs = [Question("kind", "If yes, what kind? (If you do not require work authorization sponsorship please select Not "
                           "Applicable.)", "select", True, ["CPT", "OPT", "J-1", "Other", "I don't know", "Not applicable"]),
          Question("act", "Provide your best result on ACT:", "select", True, ["I don't have ACT score", "<30", "36"]),
          Question("prog", "Please indicate which of the following programming languages you are proficient in",
                   "multiselect", True, ["Java", "C++", "Python", "None of the above"]),
          Question("spoken", "Are you proficient in any other languages below? Select All that apply?", "multiselect", True,
                   ["Spanish", "French", "None of the Above"]),
          Question("heard", "How did you first learn about Affirm as an employer?", "select", True,
                   ["Affirm’s Career Site", "Indeed", "LinkedIn", "Other"])]
    out = deterministic_answers(qs, p, {}, job={"title": "Software Engineer Intern", "locations": [], "pay": "",
                                                "sources": '["simplify"]'})
    assert out["kind"].value == "Not applicable"
    assert out["act"].value == "I don't have ACT score"
    assert "prog" not in out                                  # a resume question for Claude
    assert out["spoken"].value == ["None of the Above"]
    assert out["heard"].value.endswith("Career Site")       # best non-referral listed (2026-10-11)


def test_mistakes_reported_2026_10_06():
    """Stripe, IMC, Waymo and Five Rings answers Alex found wrong after submitting (2026-10-06)."""
    from smartapply.profile import Experience
    p = _p()
    p.experiences = [Experience(company="Acme Labs", title="Software Engineer Intern", start="2026-02", end="2026-06"),
                     Experience(company="SU Lab", title="Undergraduate Research Assistant", start="2025-01", end="2025-05", kind="research"),
                     Experience(company="PyTorch", title="Open Source Contributor", start="2025-02", end=None, kind="open_source")]
    p.defaults.relocation_cities = ["New York", "San Francisco", "Seattle"]
    p.defaults.current_offers, p.defaults.further_education = "No", "No"
    p.eeo.ethnicity, p.eeo.sexual_orientation, p.eeo.transgender = "White", "Prefer not to say", "No"
    offices = ["San Francisco, CA, United States", "Seattle, WA, United States", "New York, NY, United States",
               "I am open to working in any office"]
    yn = ["Yes", "No"]
    qs = [
        Question("loc1", "First location preference:", "select", True, offices),
        Question("loc2", "Second location preference", "select", True, offices),
        Question("loc3", "Third location preference", "select", True, offices),
        Question("title", "What is your current or previous job title?", "text", True),
        Question("fe", "Are you considering or committed to pursuing further education immediately after completing your current academic studies?",
                 "select", True, ["No", "I am considering further education after my current program", "I have committed to another degree after my current program"]),
        Question("fe2", "If so, please specify which degree you plan to pursue.", "select", False, ["Bachelor's", "Master's", "PhD or Doctorate", "Other"]),
        Question("off", "Do you currently have any offers?", "select", True, yn),
        Question("off2", 'If you answered "Yes" above, please specify relevant details (company name, job title, deadline date).', "text", True),
        Question("wa", "Do you require work authorization?", "select", True, ["Yes", "No", "Unknown"]),
        Question("wa2", "If yes, what kind? (If you do not require work authorization sponsorship please select Not Applicable.)", "select", True,
                 ["CPT", "OPT", "J-1", "Other", "I don't know", "Not applicable"]),
        Question("race", "How would you describe your racial/ethnic background?", "select", False,
                 ["East Asian", "White", "Southeast Asian", "White or European", "I don't wish to answer"], section="demographic"),
        Question("race2", "Race", "select", False, ["Decline To Self Identify", "White", "Asian"], section="eeoc"),
        Question("so", "How would you describe your sexual orientation?", "select", False,
                 ["Asexual", "Gay", "Prefer not to say", "Queer", "I don't wish to answer"], section="demographic"),
        Question("tg", "Do you identify as transgender?", "select", False, ["Yes", "No", "I don't wish to answer"], section="demographic"),
    ]
    a = deterministic_answers(qs, p, {})
    v = {k: x.value for k, x in a.items()}
    assert [v["loc1"], v["loc2"], v["loc3"]] == [offices[2], offices[0], offices[1]]
    assert v["title"] == "Software Engineer Intern"
    assert v["fe"] == "No" and a["fe2"].source == "skip" and v["fe2"] is None
    assert v["off"] == "No" and v["off2"] == "N/A"
    assert v["wa"] == "No" and v["wa2"] == "Not applicable"
    assert v["race"] == "White" and v["race2"] == "White"
    assert v["so"] == "Prefer not to say" and v["tg"] == "No"


def test_follow_up_after_an_unanswered_question_waits_for_manas():
    p = _p()
    qs = [Question("q1", "Do you have a relative who is a member of the board?", "select", True, ["Yes", "No"]),
          Question("q2", "If yes, please share your GitHub profile.", "text", True)]
    a = deterministic_answers(qs, p, {})
    assert a["q2"].source == "needs_human"


def test_masters_premise_is_answered_no_for_a_bachelors_student():
    """Capstone, 2026-10-10: "If you are currently completing your master's degree did you have a full time position
    between Undergraduate degree and your Masters?" was answered Yes by the "currently completing" rule."""
    p = _p()
    yn = ["Yes", "No"]
    qs = [Question("cap", "If you are currently completing your master’s degree did you have a full time position "
                          "between Undergraduate degree and your Masters?", "select", True, yn),
          Question("na", "If you are pursuing a PhD, have you published?", "select", True, ["Yes", "No", "N/A"]),
          Question("both", "Are you currently in process of completing your Bachelors or Masters Degree?", "select", True, yn),
          Question("bs", "Are you currently pursuing a bachelor's degree?", "select", True, yn)]
    out = deterministic_answers(qs, p, {})
    assert out["cap"].value == "No" and out["na"].value == "N/A"
    assert out["both"].value == "Yes" and out["bs"].value == "Yes"


def test_batch_mistakes_2026_10_10():
    """Geneva Trading's "May/June 2028" graduation option; Riot's "majority owned by Tencent" is not a major question."""
    p = _p()
    p.school.graduation = "2028-05"
    qs = [Question("g", "What is your expected graduation date? Please specify the month and year (e.g., December 2027)",
                   "select", True, ["May/June 2027", "December 2027", "May/June 2028", "December 2028", "Other"]),
          Question("t", "As we are majority owned by Tencent, we would like to know if you have worked for Tencent or a "
                        "company owned or otherwise affiliated with Tencent within the last 12 months.", "select", True, ["Yes", "No"])]
    out = deterministic_answers(qs, p, {})
    assert out["g"].value == "May/June 2028"
    assert "major" not in str(getattr(out.get("t"), "needs_human", "") or "")


def test_graduation_pairs_and_month_only_lists():
    """Lyft's "Spring/Summer 2028" and Whatnot's month-only list (2026-10-10)."""
    from smartapply.answers import grad_option
    from smartapply.answers import MONTHS
    assert grad_option(["December 2027", "Spring/Summer 2028", "Other"], "2028-05") == "Spring/Summer 2028"
    assert grad_option(list(MONTHS), "2028-05") == "May"
    assert grad_option(["Fall/Winter 2027", "Spring 2028"], "2028-05") == "Spring 2028"


def test_live_in_country_for_a_us_posting():
    # Adobe, 2026-10-13: left blank; he lives in the U.S. and is authorized there
    p = _p()
    p.work_auth.authorized_us = True
    q = Question("a1", "Are you able to live in the country where this role is located for the full duration of the internship "
                 "(~May - September)?", "select", True, ["Yes", "No"])
    assert deterministic_answers([q], p, {}, {"locations": '["San Jose, CA"]'})["a1"].value == "Yes"
    assert deterministic_answers([q], p, {}, {"locations": '["London, UK"]'})["a1"].value != "Yes"


def test_ip_and_partner_relatives_from_attestations():
    # Alex 2026-10-13: no intellectual property; no family at any relevant company except Initech
    p = _p()
    p.attestations.relatives_at_employer, p.attestations.owns_intellectual_property = "No", "No"
    p.attestations.relatives_employers = ["Initech"]
    qa = Question("a", "Are you an immediate family member (parent, child, sibling, spouse/partner) of a partner at Ernst & Young "
                  "who is based out of the San Jose, California office?", "select", True, ["Yes", "No"])
    qb = Question("b", "Do you own, control, or have an economic interest in any intellectual property rights (patents, "
                  "trademarks, or copyrights)?", "select", True, ["Yes", "No"])
    qc = Question("c", "Do you have any relatives currently employed by the company?", "select", True, ["Yes", "No"])
    out = deterministic_answers([qa, qb, qc], p, {}, {"company": "Intel"})
    assert (out["a"].value, out["b"].value, out["c"].value) == ("No", "No", "No")
    disney = deterministic_answers([qc], p, {}, {"company": "The Initech Company"})["c"]
    assert disney.value is None and disney.needs_human


def test_class_year_and_salary_range_expectation():
    # Intel, 2026-10-13: both left blank
    import datetime
    p = _p()
    today = datetime.date.today()
    ay_end = today.year + 1 if today.month >= 8 else today.year
    p.school.graduation = f"{ay_end + 1}-05"
    p.defaults.salary_text = "Open / negotiable"
    q1 = Question("y", "What year in college are you?", "select", True,
                  ["Select One", "First Year Undergrad", "Sophomore", "Junior", "Senior", "Senior/ Continuing to Masters"])
    q2 = Question("s", "Provide your salary/hourly range expectation.", "text", True, [])
    out = deterministic_answers([q1, q2], p, {}, {"pay": "00-95,702"})
    assert out["y"].value == "Junior"
    assert "s" not in out            # a scraped non-money pay string is never typed; Claude estimates instead
    assert deterministic_answers([q2], p, {}, {"pay": "$45-$50/hr"})["s"].value == "In line with the posted range ($45-$50/hr)"
    qn = Question("n", "Desired hourly rate", "number", True, [])
    assert deterministic_answers([qn], p, {}, {"pay": "$45-$50/hr"})["n"].value == "45"


def test_english_proficiency_prefers_native():
    """Alex, 2026-10-13: native English speaker, so Native over Fluent; else the highest level offered."""
    p = _p()
    qs = [Question("a", "English proficiency level", "select", True, ["Basic", "Intermediate", "Fluent", "Native"]),
          Question("b", "What is your level of English?", "select", True, ["Non-native", "Advanced", "Fluent"]),
          Question("c", "Rate your English language skills", "select", True, ["Limited", "Good", "Professional"])]
    out = deterministic_answers(qs, p, {}, job={"title": "Software Engineer Intern", "locations": [], "pay": ""})
    assert out["a"].value == "Native"
    assert out["b"].value == "Fluent"
    assert out["c"].value == "Professional"


def test_area_of_interest_is_not_the_degree():
    """Intel, 2026-10-13: the degree rule typed "Bachelor of Science in Computer Science" into an interests box."""
    p = _p()
    q = Question("i", "What kind of opportunities are you looking for? Describe your area of interest (type of position "
                      "for which you are looking) or degree focus area(s).", "textarea", True, [])
    d = Question("d", "What degree are you pursuing?", "text", True, [])
    out = deterministic_answers([q, d], p, {}, job={"title": "Software Engineer Intern", "locations": [], "pay": ""})
    assert "i" not in out and out["d"].value.endswith(p.school.major)


def test_simple_questions_answered_without_claude():
    """2026-10-14 no-Claude batch: questions the profile answers that had gone to Claude."""
    p = _p()
    us = {"title": "Software Engineer Intern", "locations": ["Houston, TX"], "pay": ""}
    qs = [Question("n", "Please state your full legal name:", "text", True, []),
          Question("s", "Current school or university:", "text", True, []),
          Question("ds", 'What is your school? (Select "Other" if it is not in list)', "select", True,
                   ["Cornell University", p.school.name, "Other"]),
          Question("ph", "Please provide your phone number", "text", True, []),
          Question("g", "I have United States government or public institution employment experience (federal, state, "
                        "or local) either as an employee or contractor", "select", True, ["Yes", "No"]),
          Question("w", "Do you possess a valid work permit in the country for which the position resides?", "select",
                   True, ["Yes", "No", "Not Applicable"]),
          Question("ws", "Do you prefer a winter or summer internship?", "select", True, ["Winter 2027", "Summer 2027"])]
    out = deterministic_answers(qs, p, {}, job=us)
    assert out["n"].value == p.full_name and out["s"].value == p.school.name and out["ds"].value == p.school.name
    assert out["ph"].value == p.phone_digits and out["w"].value == "Yes" and out["ws"].value == "Summer 2027"
    assert out["g"].value == p.attestations.government_employment_5y
    abroad = deterministic_answers(qs[5:6], p, {}, job={**us, "locations": ["London, UK"]})
    assert "w" not in abroad or abroad["w"].value != "Yes"


def test_grad_option_month_ranges_and_academic_years():
    assert grad_option(["Sept - Dec 2027", "Jan - April 2028", "May - Aug 2028", "Other"], "2028-05") == "May - Aug 2028"
    assert grad_option(["Fall 2027", "2028 academic school year", "2029 academic school year"], "2028-05") \
        == "2028 academic school year"


def test_lgbtq_is_no_not_decline():
    """DraftKings, 2026-10-14: "No" for a heterosexual, non-transgender profile, never "I prefer not to disclose"."""
    p = _p()
    p.eeo.sexual_orientation, p.eeo.transgender = "Heterosexual", "No"
    q = Question("l", "Do you identify as LGBTQ+?", "select", True,
                 ["I do identify as LGBTQ+", "I do not identify as LGBTQ+", "I prefer not to disclose"])
    q2 = Question("m", "Are you a member of the LGBTQ+ community?", "select", False, ["Yes", "No", "Prefer not to say"])
    out = deterministic_answers([q, q2], p, {}, job={"title": "Intern", "locations": [], "pay": ""})
    assert out["l"].value == "I do not identify as LGBTQ+" and out["m"].value == "No"


def test_major_with_why_is_not_just_the_major():
    """Khan Academy, 2026-10-14: "What is your current major, and what inspired you ..." got "Computer Science"."""
    p = _p()
    q = Question("m", "What is your current major, and what inspired you to choose this field of study?", "textarea", True, [])
    plain = Question("p", "Major", "text", True, [])
    out = deterministic_answers([q, plain], p, {}, job={"title": "Intern", "locations": [], "pay": ""})
    assert "m" not in out and out["p"].value == p.school.major


def test_younger_than_18_is_no():
    """GE HealthCare 2026-10-15: "Are you younger than 18 years old?" was answered Yes by the over-18 rule."""
    p = _p()
    qs = [Question("u", "Are you younger than 18 years old? Note: this question is used to avoid child labor issues",
                   "select", True, ["Yes", "No"]),
          Question("o", "Are you 18 years of age or older?", "select", True, ["Yes", "No"]),
          Question("s", "Are you 18 years or older? If you are under 18, hire is subject to verification", "select", True,
                   ["Yes", "No"])]
    out = deterministic_answers(qs, p, {}, job={"title": "Intern", "locations": ["Waukesha, WI"], "pay": ""})
    assert out["u"].value == "No" and out["o"].value == "Yes" and out["s"].value == "Yes"


def test_notice_period_is_shortest():
    p = _p()
    qs = [Question("n", "What is your notice period?*", "select", True,
                   ["Less than 30 days", "30 to 59 days", "60 to 89 days", "90 or more days"])]
    out = deterministic_answers(qs, p, {}, job={"title": "Intern", "locations": ["NYC"], "pay": ""})
    assert out["n"].value == "Less than 30 days"


def test_relocation_assistance_is_not_relocation():
    p = _p()
    qs = [Question("w", "Are you willing to relocate for the position?*", "select", True, ["Yes", "No"]),
          Question("a", "If yes, do you require relocation assistance?", "select", False, ["Yes", "No"])]
    out = deterministic_answers(qs, p, {}, job={"title": "Intern", "locations": ["NYC"], "pay": ""})
    assert out["w"].value == "Yes" and ("a" not in out or out["a"].value != "Yes")


def test_todays_date_notice_is_a_date_not_eeo():
    # S&P HiredScore, 2026-10-15: the notice mentions disability accommodation; the field wants today's date
    from datetime import date
    p = _p()
    q = Question("h", "Candidate Consent: Use of AI-Assisted Screening Tool (HiredScore). External candidates can email "
                      "EEO.Compliance to request a disability accommodation. Enter today’s date below to confirm that "
                      "you have read and understand the above information.*", "date", True, [])
    out = deterministic_answers([q], p, {}, job={"title": "Intern", "locations": ["NYC"], "pay": ""})
    assert out["h"].value == date.today().isoformat()


def test_housing_is_no_and_commute_is_yes():
    # Alex, 2026-10-16: never ask for housing on the form; "able to commute" is Yes because he relocates
    p = _p()
    qs = [Question("h", "Will you require housing if hired?*", "select", True, ["Yes", "No"]),
          Question("c", "Are you able to commute to our Plantation, IL office?*", "select", True, ["Yes", "No"])]
    out = deterministic_answers(qs, p, {}, job={"title": "Intern", "locations": ["Plantation, IL"], "pay": ""})
    assert out["h"].value == "No"
    assert out["c"].value == "Yes"          # "located within a commutable distance" stays No (test_logistics)
