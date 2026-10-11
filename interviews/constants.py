"""
Every timing/config knob for interview scheduling lives here. Nothing in
models/services/views should hardcode a duration or window - change it here
and the whole module follows.
"""

# --- Scheduling ------------------------------------------------------------
DEFAULT_INTERVIEW_DURATION_MINUTES = 45

# An interviewer may not hold two interviews whose windows overlap. The buffer
# is padding either side of each booking, so back-to-back slots still leave
# room to write up notes and dial into the next call.
DOUBLE_BOOKING_BUFFER_MINUTES = 10

# Guards against scheduling something that has effectively already started.
MIN_SCHEDULING_LEAD_MINUTES = 15

# --- Feedback --------------------------------------------------------------
# Feedback unlocks once the slot has started - an interviewer shouldn't be able
# to file a verdict on a conversation that hasn't happened yet.
FEEDBACK_UNLOCK_MINUTES_AFTER_START = 0
RATING_MIN = 1
RATING_MAX = 5

# --- Google Calendar / Meet ------------------------------------------------
GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
GOOGLE_CALENDAR_ID = "primary"
# Calendar API requires this version header to return conferenceData (the Meet link).
GOOGLE_CONFERENCE_DATA_VERSION = 1
