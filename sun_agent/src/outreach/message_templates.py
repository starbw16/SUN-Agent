"""
Lapsed-client SMS message templates, organised by how long ago the last visit was.

Each template is a plain string with {first_name}, {store_name}, and {booking_url}
placeholders. The rotation engine in sms_sender picks the next unused template per
client so they never get the same message twice in a row.

Tiers map to retention_windows_days values:
  TIER_4W  — 28-day window  (about 4 weeks out)
  TIER_6W  — 42-day window  (about 6 weeks out)
  TIER_8W  — 56-day window  (8+ weeks — win-back tone)
"""

TIER_4W = [
    "Hey {first_name}! It's been about a month since your last visit at {store_name} — time for a fresh cut? Book here: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! The crew at {store_name} wanted to check in — hair grows fast! Ready for a trim? {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! Our calendar says it might be time for another cut at {store_name}. Grab a spot: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! It's been about 4 weeks — {store_name} is ready when you are. Book here: {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! Quick note from {store_name} — come on in and let's get you looking sharp! {booking_url} Reply STOP to opt out.",
    "Hi {first_name}, it's {store_name}! The team misses you. Ready to book your next cut? {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! Friendly heads-up from {store_name} — about 4 weeks since your last cut. Looking for a time? {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! Hair doesn't wait and neither do we 😄 {store_name} has openings — grab a spot: {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! It's been a little while. {store_name} has openings this week — come get a fresh cut! {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! A month goes by fast. {store_name} is ready for your next visit — book in a few taps: {booking_url} Reply STOP to opt out.",
]

TIER_6W = [
    "Hey {first_name}! It's been about 6 weeks since we've seen you at {store_name}. Time to get back in the chair! {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! Six weeks is a long time in hair time 😄 {store_name} misses you — let's get you booked: {booking_url} Reply STOP to opt out.",
    "Hey {first_name}, the team at {store_name} is thinking about you! It's been a little while — come on in: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! {store_name} here — it's been 6 weeks since your last cut. We've got great availability right now: {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! Your hair's been patient enough 😄 Come see us at {store_name}: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! A friendly check-in from {store_name} — about 6 weeks since your last visit. Ready to come back in? {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! {store_name} wants to see you again — it's been a bit! Easy online booking here: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! Six weeks is tough to go without a trim! {store_name} has a spot with your name on it: {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! We miss seeing you at {store_name}. Come on back — book your next cut here: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! It's been about 6 weeks. {store_name} has you covered — grab a time that works: {booking_url} Reply STOP to opt out.",
]

TIER_8W = [
    "Hey {first_name}! It's been a couple months since your last visit to {store_name}. We'd love to see you back — book here: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! {store_name} misses you — it's been about 8 weeks! Come on back and let's get you looking great: {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! Two months is a long time! The team at {store_name} would love to see you again. Book today: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}, it's been a while! We haven't seen you at {store_name} in about 2 months. Lots of great availability — let's get you back in: {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! A lot can change in 8 weeks — including hair! {store_name} is here when you're ready: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! We wanted to reach out — it's been about 2 months since your last cut at {store_name}. Would love to have you back: {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! {store_name} checking in. It's been a little while — we've saved a spot for you: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! Two months is a long stretch! {store_name} is ready for your return — book a time: {booking_url} Reply STOP to opt out.",
    "Hey {first_name}! We've been thinking about you at {store_name}. Come on back — it's been about 8 weeks and we'd love to see you: {booking_url} Reply STOP to opt out.",
    "Hi {first_name}! We miss you! It's been 2 months since your last visit to {store_name}. Great spots available — book today: {booking_url} Reply STOP to opt out.",
]

# Maps retention window (days) → template list
# Keys match retention_windows_days config values
TEMPLATES_BY_WINDOW: dict[int, list[str]] = {
    28: TIER_4W,
    42: TIER_6W,
    56: TIER_8W,
}


def get_templates(window_days: int) -> list[str]:
    """Return the template list for the given window, falling back to nearest tier."""
    if window_days in TEMPLATES_BY_WINDOW:
        return TEMPLATES_BY_WINDOW[window_days]
    # Nearest tier fallback
    if window_days <= 35:
        return TIER_4W
    if window_days <= 49:
        return TIER_6W
    return TIER_8W


def _normalize_url(url: str) -> str:
    if not url:
        return "[booking link]"
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def render(template: str, first_name: str, store_name: str, booking_url: str) -> str:
    return template.format(
        first_name=first_name,
        store_name=store_name,
        booking_url=_normalize_url(booking_url),
    )
