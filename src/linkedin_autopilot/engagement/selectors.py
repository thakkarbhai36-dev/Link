"""CSS selectors for the LinkedIn web UI, all in one file on purpose.

LinkedIn ships front-end changes constantly and these will break. When a scrape
starts returning nothing, this is the only file you should need to edit: run
`autopilot engage scan --debug` to save a screenshot and the page HTML, open the
page in the browser the tool launched, and update the entry that no longer
matches. Each field lists fallbacks tried in order.
"""

from __future__ import annotations

# Containers that represent one post in the main feed.
POST_CONTAINERS = (
    "div.feed-shared-update-v2",
    'div[data-id^="urn:li:activity:"]',
    'div[data-urn^="urn:li:activity:"]',
)

# Attributes carrying the post URN, checked in order.
URN_ATTRIBUTES = ("data-urn", "data-id")

AUTHOR_NAME = (
    ".update-components-actor__title span[aria-hidden='true']",
    ".update-components-actor__title",
    ".update-components-actor__name",
)

AUTHOR_LINK = (
    "a.update-components-actor__meta-link",
    ".update-components-actor__container a[href*='/in/']",
    "a.update-components-actor__image",
)

# Contains the connection degree, e.g. "1st", and the job title.
AUTHOR_DESCRIPTION = (
    ".update-components-actor__description",
    ".update-components-actor__supplementary-actor-info",
)

# Relative timestamp plus, sometimes, the word "Promoted".
POST_AGE = (
    ".update-components-actor__sub-description span[aria-hidden='true']",
    ".update-components-actor__sub-description",
)

POST_TEXT = (
    ".update-components-text",
    ".feed-shared-inline-show-more-text",
    ".update-components-update-v2__commentary",
)

# Expands a truncated post body.
SEE_MORE_BUTTON = (
    "button.feed-shared-inline-show-more-text__see-more-less-toggle",
    "button.inline-show-more-text__button",
)

LIKE_BUTTON = (
    "button.react-button__trigger",
    "button[aria-label^='React Like']",
    "button[aria-label='Like']",
)

COMMENT_BUTTON = (
    "button[aria-label*='Comment']",
    "button.comment-button",
)

COMMENT_EDITOR = (
    "div.comments-comment-box div.ql-editor[contenteditable='true']",
    "div.ql-editor[contenteditable='true']",
    "div[role='textbox'][contenteditable='true']",
)

COMMENT_SUBMIT = (
    "button.comments-comment-box__submit-button--cr",
    "button[class*='comments-comment-box__submit-button']",
    "button.comments-comment-box__submit-button",
)

# Present only when the session is logged in.
LOGGED_IN_MARKERS = (
    "#global-nav",
    "div.feed-identity-module",
    "button[aria-label*='profile']",
)

FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL = "https://www.linkedin.com/login"
