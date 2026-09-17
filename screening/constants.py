"""
Every tunable knob for resume scoring lives here - weights, thresholds, tier
bands and the alias map. Nothing in services.py or views.py should hardcode a
number; change it here and the whole engine follows.
"""

# --- Score weighting -------------------------------------------------------
# Skills alone drive the numeric score. Experience/education are reported as
# separate pass/fail badges and deliberately NOT folded into the percentage,
# so the number always means one thing: "how well do the skills line up".
REQUIRED_SKILL_WEIGHT = 80
NICE_TO_HAVE_WEIGHT = 20

# --- Fuzzy matching --------------------------------------------------------
# Recruiters free-type skill tags on the job posting form (get_or_create), so
# the master Skill table can legitimately contain near-duplicates like
# "ReactJS" and "React". Fuzzy matching bridges those plus minor typos.
# 85 is tight enough that "React"/"Angular" stay distinct.
FUZZY_MATCH_THRESHOLD = 85

# Normalized on both sides before comparison, so keys/values are lowercase.
# Extend freely - this is the cheapest correctness lever in the whole module.
SKILL_ALIASES = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "postgres": "postgresql",
    "psql": "postgresql",
    "reactjs": "react",
    "react.js": "react",
    "nodejs": "node",
    "node.js": "node",
    "expressjs": "express",
    "golang": "go",
    "c++": "cpp",
    "c#": "csharp",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "k8s": "kubernetes",
    "gcp": "google cloud",
    "aws": "amazon web services",
    "html5": "html",
    "css3": "css",
    "restful": "rest",
    "rest api": "rest",
}

# --- Fit tiers -------------------------------------------------------------
# Bands for the coloured chip on each row. Advisory only - nothing in this
# module ever auto-shortlists or auto-rejects on the back of a tier.
TIER_STRONG_MIN = 75
TIER_POTENTIAL_MIN = 50

TIER_STRONG = "strong"
TIER_POTENTIAL = "potential"
TIER_WEAK = "weak"

TIER_LABELS = {
    TIER_STRONG: "Strong Fit",
    TIER_POTENTIAL: "Potential Fit",
    TIER_WEAK: "Weak Fit",
}

# --- Parse confidence ------------------------------------------------------
# A resume the parser barely understood would otherwise score near-zero and
# look like a bad candidate. Below this many parsed skills we flag the row
# "review manually" instead of letting the score speak for itself.
LOW_SIGNAL_SKILL_COUNT = 3

# --- Education ranking -----------------------------------------------------
# Maps the job posting's EducationLevel choice to a comparable rank.
EDUCATION_LEVEL_RANK = {
    "ANY": 0,
    "HIGH_SCHOOL": 1,
    "BACHELORS": 2,
    "MASTERS": 3,
    "PHD": 4,
}

# Candidate degrees are free text off a resume ("B.Tech", "Master of Science").
# Longest/most-specific keywords first so "master of science" wins over "ms".
DEGREE_KEYWORD_RANK = [
    ("doctor of philosophy", 4),
    ("ph.d", 4),
    ("phd", 4),
    ("master of business administration", 3),
    ("master of technology", 3),
    ("master of science", 3),
    ("master of arts", 3),
    ("master", 3),
    ("m.tech", 3),
    ("m.sc", 3),
    ("msc", 3),
    ("mba", 3),
    ("mca", 3),
    ("m.a", 3),
    ("m.e", 3),
    ("bachelor of technology", 2),
    ("bachelor of engineering", 2),
    ("bachelor of science", 2),
    ("bachelor of arts", 2),
    ("bachelor", 2),
    ("b.tech", 2),
    ("b.sc", 2),
    ("bsc", 2),
    ("bca", 2),
    ("b.a", 2),
    ("b.e", 2),
    ("diploma", 1),
    ("high school", 1),
]

# --- Badge verdicts --------------------------------------------------------
# UNKNOWN is a first-class outcome, not a failure. Candidate education/experience
# come from resume parsing that deliberately leaves fields blank rather than
# guessing, so "we couldn't tell" must never render as "candidate fails".
VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_UNKNOWN = "unknown"
