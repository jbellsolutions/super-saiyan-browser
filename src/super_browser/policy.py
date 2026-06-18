from __future__ import annotations

import re

from .models import RiskLevel, TaskSpec


EXTERNAL_WRITE_KEYWORDS = (
    "post",
    "posting",
    "comment",
    "commenting",
    "send",
    "sending",
    "message",
    "messaging",
    "dm",
    "reply",
    "respond",
    "submit",
    "publish",
    "upload",
    "like",
    "react",
    "upvote",
    "downvote",
    "star this",
    "star repo",
    "star repository",
    "watch repo",
    "watch repository",
    "fork this",
    "fork repo",
    "fork repository",
    "bookmark",
    "save post",
    "save this post",
    "save item",
    "save this item",
    "pin post",
    "pin this post",
    "unpin",
    "follow",
    "following",
    "unfollow",
    "block user",
    "block this user",
    "block profile",
    "unblock",
    "mute thread",
    "mute this thread",
    "mute user",
    "unmute",
    "report post",
    "report this post",
    "report profile",
    "report this profile",
    "report user",
    "report this user",
    "share",
    "sharing",
    "share to story",
    "add to story",
    "post story",
    "repost",
    "reposting",
    "retweet",
    "retweeting",
    "quote post",
    "quote-post",
    "quote tweet",
    "quote retweet",
    "join",
    "leave",
    "join group",
    "leave group",
    "create group",
    "create a group",
    "create event",
    "create an event",
    "create page",
    "create a page",
    "accept request",
    "accept connection",
    "connection request",
    "send connection request",
    "friend request",
    "add friend",
    "add as friend",
    "add this person as a friend",
    "remove friend",
    "unfriend",
    "follow request",
    "decline request",
    "reject request",
    "cancel request",
    "confirm request",
    "approve request",
    "approve this group request",
    "remove connection",
    "remove this connection",
    "remove this linkedin connection",
    "remove follower",
    "remove this follower",
    "endorse",
    "rate this",
    "vote in poll",
    "vote yes",
    "vote no",
    "clap for",
    "add to favorites",
    "add this item to favorites",
    "favorite this",
    "rsvp",
    "attend event",
    "check in",
    "interested in event",
    "mark interested",
    "mark interested in this event",
    "tag this",
    "tag user",
    "mention user",
    "mention this person",
    "reserve",
    "reserving",
    "sign up",
    "register",
    "subscribe",
    "unsubscribe",
    "purchase",
    "buy",
    "checkout",
    "donate",
    "bid",
    "boost post",
    "promote post",
    "create ad",
    "launch ad",
    "run ad",
    "delete",
    "update profile",
    "edit profile",
    "update bio",
    "change bio",
    "change username",
    "change email",
    "change profile picture",
    "change avatar",
    "set profile picture",
    "set status",
    "change password",
    "create account",
    "deactivate account",
    "disable account",
    "close account",
    "connect with",
)

CREDENTIAL_KEYWORDS = (
    "login",
    "log in",
    "logged in",
    "logged-in",
    "logged into",
    "sign in",
    "signed in",
    "signed-in",
    "authenticated",
    "authentication",
    "auth",
    "credentials",
    "2fa",
    "otp",
    "passkey",
    "password",
    "cookie",
    "session",
    "oauth",
    "token",
    "api key",
    "access token",
    "bearer token",
    "client secret",
    "private key",
    "secret key",
    "service account key",
    "chrome profile",
    "browser profile",
    "local profile",
    "user profile",
    "my profile",
    "existing profile",
    "my account",
    "account settings",
    "account profile",
    "private",
    "member-only",
)
CREDENTIAL_PATTERNS = (
    r"\bmy (?:linkedin |facebook |instagram |x |twitter |reddit |slack |discord |)?(?:messages|dms|inbox)\b",
    r"\b(?:private|direct) messages\b",
    r"\b(?:private|direct) dms\b",
    r"\binbox\b",
)
LONG_RUNNING_KEYWORDS = ("monitor", "scheduled", "long-running", "overnight", "every day", "recurring", "crawl")
DRAFT_ONLY_KEYWORDS = (
    "draft only",
    "do not publish",
    "don't publish",
    "do not post",
    "don't post",
    "do not comment",
    "don't comment",
    "do not reply",
    "don't reply",
    "do not respond",
    "don't respond",
    "do not message",
    "don't message",
    "do not dm",
    "don't dm",
    "do not send",
    "don't send",
    "do not submit",
    "don't submit",
    "without publishing",
    "without posting",
    "without commenting",
    "without replying",
    "without responding",
    "without messaging",
    "without dming",
    "without sending",
    "without submitting",
    "not publish",
    "not post",
    "not comment",
    "not reply",
    "not respond",
    "not message",
    "not dm",
    "not send",
    "not submit",
    "but do not publish",
    "but do not post",
    "but do not comment",
    "but do not reply",
    "but do not respond",
    "but do not message",
    "but do not dm",
    "but do not send",
    "but do not submit",
    "stop before publishing",
    "stop before posting",
    "stop before commenting",
    "stop before replying",
    "stop before responding",
    "stop before messaging",
    "stop before dming",
    "stop before sending",
    "stop before submitting",
    "stop before publish",
    "stop before post",
    "stop before comment",
    "stop before reply",
    "stop before respond",
    "stop before message",
    "stop before dm",
    "stop before send",
    "stop before submit",
)
DRAFT_TEXT_KEYWORDS = ("draft", "prepare", "type", "write", "fill", "put it in", "create")
NON_DRAFTABLE_WRITE_KEYWORDS = (
    "upload",
    "like",
    "react",
    "upvote",
    "downvote",
    "star this",
    "star repo",
    "star repository",
    "watch repo",
    "watch repository",
    "fork this",
    "fork repo",
    "fork repository",
    "bookmark",
    "save post",
    "save this post",
    "save item",
    "save this item",
    "pin post",
    "pin this post",
    "unpin",
    "follow",
    "following",
    "unfollow",
    "block user",
    "block this user",
    "block profile",
    "unblock",
    "mute thread",
    "mute this thread",
    "mute user",
    "unmute",
    "report post",
    "report this post",
    "report profile",
    "report this profile",
    "report user",
    "report this user",
    "share",
    "sharing",
    "share to story",
    "add to story",
    "post story",
    "repost",
    "reposting",
    "retweet",
    "retweeting",
    "join",
    "leave",
    "join group",
    "leave group",
    "create group",
    "create a group",
    "create event",
    "create an event",
    "create page",
    "create a page",
    "accept request",
    "accept connection",
    "connection request",
    "send connection request",
    "friend request",
    "add friend",
    "add as friend",
    "add this person as a friend",
    "remove friend",
    "unfriend",
    "follow request",
    "decline request",
    "reject request",
    "cancel request",
    "confirm request",
    "approve request",
    "approve this group request",
    "remove connection",
    "remove this connection",
    "remove this linkedin connection",
    "remove follower",
    "remove this follower",
    "endorse",
    "rate this",
    "vote in poll",
    "vote yes",
    "vote no",
    "clap for",
    "add to favorites",
    "add this item to favorites",
    "favorite this",
    "rsvp",
    "attend event",
    "check in",
    "interested in event",
    "mark interested",
    "mark interested in this event",
    "tag this",
    "tag user",
    "mention user",
    "mention this person",
    "reserve",
    "reserving",
    "sign up",
    "register",
    "subscribe",
    "unsubscribe",
    "purchase",
    "buy",
    "checkout",
    "donate",
    "bid",
    "boost post",
    "promote post",
    "create ad",
    "launch ad",
    "run ad",
    "delete",
    "update profile",
    "edit profile",
    "update bio",
    "change bio",
    "change username",
    "change email",
    "change profile picture",
    "change avatar",
    "set profile picture",
    "set status",
    "change password",
    "create account",
    "deactivate account",
    "disable account",
    "close account",
    "connect with",
)
EXTERNAL_WRITE_PATTERNS = (
    r"\bcreate (?:a |an )?(?:[a-z0-9-]+ ){0,3}(?:group|event|page)\b",
    r"\bcreate (?:a |an )?lead(?!\s+magnet)\b(?: .{0,80}\b)?",
    r"\bcreate (?:a |an )?(?:prospect|contact|customer|deal|opportunity)(?:\b| .{0,80}\b)",
    r"\badd .{1,80} as (?:a )?friend\b",
    r"\badd .{1,80} to (?:the )?(?:(?:shopping )?cart|basket|bag|wish\s*list|wishlist|waitlist|crm|salesforce|hubspot)\b",
    r"\bremove .{1,80} from (?:the )?(?:(?:shopping )?cart|basket|bag|wish\s*list|wishlist|waitlist)\b",
    r"\b(?:change|update|set) .{0,40}(?:quantity|qty).{0,40}(?:(?:shopping )?cart|basket|bag|order)\b",
    r"\b(?:set|update|change) .{0,40}(?:shipping|delivery|billing) address\b",
    r"\b(?:apply|add|enter|use) .{0,40}(?:promo|promotion|coupon|discount|voucher) code .{0,80}(?:(?:shopping )?cart|checkout|order|basket|bag)\b",
    r"\b(?:redeem|claim) .{0,40}(?:coupon|offer|discount|reward|voucher|deal|promotion)\b",
    r"\b(?:place|submit|complete|confirm) (?:the |this |my |your )?(?:order|purchase)\b",
    r"\bpay (?:this|that|the|my|your)? .{0,40}(?:invoice|bill|balance|order|checkout|subscription|account)\b",
    r"\b(?:preorder|pre-order|pre order) .{0,80}(?:product|item|release|book|ticket)\b",
    r"\brequest (?:a |an |the )?(?:refund|return|exchange) for .{0,80}(?:order|purchase|item|product)\b",
    r"\bcancel (?:this|that|the|my|your)? .{0,40}(?:order|purchase|reservation|booking|appointment)\b",
    r"\binvite .{1,80} to .{1,80}(?:workspace|team|group|event|meeting|calendar|channel|account|organization|project)\b",
    r"\binvite .{0,80}(?:user|member|person|people|lead|prospect|contact|candidate|client|customer)\b",
    r"\bremove .{1,80} follower\b",
    r"\bremove .{0,80}(?:member|user|person).{0,80}(?:group|workspace|team|channel)\b",
    r"\bapprove .{1,80} request\b",
    r"\b(?:accept|decline|reject|cancel|confirm|approve) .{1,80}(?:invite|invitation)\b",
    r"\bconnect (?:with|to) .{0,60}(?:prospect|person|user|profile|lead|contact|candidate|linkedin)\b",
    r"\bwrite (?:a )?review\b(?!\s+(?:summary|response|outline|template|copy|analysis|article)\b)",
    r"\bleave (?:a )?review\b(?!\s+(?:summary|response|outline|template|copy|analysis|article)\b)",
    r"\bsave .{0,40}(?:post|item|result|listing|profile)\b",
    r"\badd .{1,80} to favorites\b",
    r"\bmark interested(?: .{1,80})?\b",
    r"\bmark (?:myself|me|this event|.{1,40} event) as going\b",
    r"\bmark .{0,80}(?:email|message|thread|conversation).{0,40} as (?:read|unread|spam|important)\b",
    r"\bmark .{1,80} as (?:contacted|qualified|disqualified|won|lost|done|complete|completed)\b",
    r"\bmove .{1,80} to .{1,80}(?:stage|status|pipeline|campaign|sequence|list)\b",
    r"\bmove .{0,80}(?:email|message|thread|conversation).{0,40} to .{0,40}(?:archive|trash|spam|folder|label)\b",
    r"\barchive .{0,80}(?:email|message|thread|conversation)\b",
    r"\badd .{0,30}(?:lead|prospect|contact|customer).{0,50} to .{1,80}(?:list|campaign|sequence|pipeline|crm)\b",
    r"\b(?:write|export|sync|push|import) .{0,80}(?:lead|leads|prospect|prospects|contact|contacts|customer|customers).{0,80} to .{0,80}(?:crm|salesforce|hubspot|pipedrive|zoho|apollo)\b",
    r"\bassign .{0,40}(?:lead|prospect|contact|customer|deal|opportunity).{0,40}\b",
    r"\benroll .{0,40}(?:lead|prospect|contact|customer|person).{0,80}(?:campaign|sequence|workflow|automation)\b",
    r"\b(?:create|open) (?:a |an |the )?(?:github |gitlab |jira |linear |trello |asana |notion )?(?:issue|ticket|task|card)\b",
    r"\b(?:close|reopen|resolve) .{0,80}(?:issue|ticket|task|card)\b",
    r"\bupdate .{0,40}status .{0,40}(?:issue|ticket|task|card)\b",
    r"\bupdate .{0,80}(?:issue|ticket|task|card).{0,40}status\b",
    r"\b(?:add|remove|set) .{0,40}label .{0,80}(?:issue|ticket|task|card|pull request|pr)\b",
    r"\bassign .{0,80}(?:issue|ticket|task|card|pull request|pr).{0,80}\b",
    r"\bmove .{0,80}(?:issue|ticket|task|card).{0,80} to .{0,40}(?:done|todo|to do|in progress|backlog|blocked|closed|open|review|qa|completed)\b",
    r"\b(?:create|open|merge|close|reopen) (?:a |an |the |this )?(?:pull request|pr)\b",
    r"\brequest review .{0,80}(?:pull request|pr)\b",
    r"\b(?:create|archive|transfer|rename) .{0,80}(?:repository|repo)\b",
    r"\bcreate (?:a |an |the )?(?:google drive |dropbox |onedrive |box |sharepoint )?folder\b",
    r"\b(?:rename|move|copy) .{0,80}(?:file|folder|document|doc|spreadsheet|sheet|slide|deck)\b",
    r"\b(?:trash|restore) .{0,80}(?:file|folder|document|doc|spreadsheet|sheet|slide|deck)(?: .{0,40}trash)?\b",
    r"\bmake .{0,80}(?:file|folder|document|doc|spreadsheet|sheet|slide|deck).{0,40}(?:public|shared|private|anyone with the link)\b",
    r"\b(?:grant|give|revoke|remove|change|set) .{0,80}(?:access|permission|permissions|sharing|editor|viewer|commenter).{0,80}(?:file|folder|document|doc|spreadsheet|sheet|calendar|app|application|integration|user|account|workspace|anyone with the link)\b",
    r"\b(?:grant|give|revoke|remove) .{0,80}access (?:for|to|from) .{0,80}(?:user|person|app|application|integration|calendar|file|folder|document|doc)\b",
    r"\binstall .{0,80}(?:app|application|extension|plugin|integration)\b",
    r"\b(?:authorize|connect|disconnect|enable|disable) .{0,80}(?:app|application|integration|extension|plugin|oauth|calendar|drive|slack|google|workspace)\b",
    r"\b(?:change|update|save|set) .{0,80}(?:settings|preferences|notification preferences|account settings|privacy settings|sharing settings)\b",
    r"\b(?:create(?!\s+local notes about)|rename|archive|unarchive) .{0,80}(?:channel|workspace|server|community|notion page|page)\b",
    r"\badd .{0,80}(?:user|member|person|people|teammate|account).{0,80} to .{0,80}(?:channel|workspace|team|server|community|organization|org)\b",
    r"\b(?:kick|ban|unban) .{0,80}(?:user|member|person|people|account).{0,80}(?:from|in) .{0,80}(?:channel|workspace|team|server|community|group|organization|org)\b",
    r"\b(?:make|promote|demote) .{0,80}(?:user|member|person|people|account|admin|moderator).{0,80}(?:admin|administrator|owner|moderator|member|viewer|editor)\b",
    r"\b(?:change|set|update) .{0,80}(?:role|roles|user role|member role|permissions?).{0,80}(?:admin|administrator|owner|moderator|member|viewer|editor)\b",
    r"\b(?:lock|unlock) .{0,80}(?:thread|conversation|channel|comments?|post)\b",
    r"\b(?:pin|unpin) .{0,80}(?:message|thread|conversation|channel|comment)\b",
    r"\b(?:generate|create(?!\s+local notes about)|rotate|regenerate|revoke|disable|enable) .{0,80}(?:api key|access token|bearer token|client secret|private key|secret key|service account key)\b",
    r"\b(?:create(?!\s+local notes about)|add|update|change|remove|disable|enable) .{0,80}webhook\b",
    r"\b(?:create(?!\s+local notes about)|trigger|start|promote|rollback|roll back|redeploy) .{0,80}(?:deployment|deploy)\b",
    r"\bdeploy .{0,80}(?:to|in) .{0,40}(?:production|prod|staging)\b",
    r"\b(?:create(?!\s+local notes about)|add|update|change|remove) .{0,80}(?:dns record|domain record|mx record|a record|aaaa record|cname record|txt record|spf record|dkim record|dmarc record|nameservers?|name servers?)\b",
    r"\b(?:create(?!\s+local notes about)|add|set|update|change|remove) .{0,80}(?:environment variable|environment variables|env var|env vars)\b",
    r"\b(?:start|activate) .{0,40}(?:free trial|trial)\b",
    r"\b(?:upgrade|downgrade|change|renew) .{0,80}(?:billing plan|subscription|plan)\b",
    r"\b(?:add|update|change|set) .{0,80}(?:payment method|credit card|card)\b",
    r"\bsell .{0,80}(?:shares?|stocks?|equity|options?|contracts?|crypto|coins?|tokens?|position|btc|eth|sol|aapl|tsla|nvda)\b",
    r"\bplace .{0,40}(?:market|limit|stop|stop-loss|take-profit)?\s*order .{0,80}(?:stock|shares?|options?|contracts?|crypto|coins?|tokens?|btc|eth|sol|aapl|tsla|nvda)\b",
    r"\b(?:open|close|liquidate) .{0,80}(?:long|short|options?|margin|futures?|crypto|trading|brokerage)?\s*position\b",
    r"\bswap .{0,40}(?:btc|eth|usdc|usdt|sol|crypto|coins?|tokens?) .{0,20}for .{0,40}(?:btc|eth|usdc|usdt|sol|crypto|coins?|tokens?)\b",
    r"\b(?:stake|unstake) .{0,80}(?:btc|eth|sol|crypto|coins?|tokens?)\b",
    r"\b(?:withdraw|deposit) .{0,80}(?:funds?|money|cash|usd|dollars?|\$|bank account|brokerage account|wallet|crypto|btc|eth|sol)\b",
    r"\btransfer .{0,80}(?:funds?|money|cash|usd|dollars?|\$|wire|ach|bank|crypto|btc|eth|sol).{0,80}(?:recipient|account|wallet|bank|address|vendor|payee)\b",
    r"\bpay .{0,80}(?:vendor|recipient|payee|contractor|invoice|bill).{0,80}(?:ach|wire|bank transfer|payout|payment)\b",
    r"\b(?:send|initiate|submit) .{0,80}(?:wire transfer|ach transfer|bank transfer|payout|withdrawal|deposit)\b",
    r"\b(?:add|update|change|set|connect|link) .{0,80}(?:bank account|brokerage account|wallet|payout account|payout method|withdrawal account|deposit account)\b",
    r"\b(?:sign|e-sign|esign|certify|attest) .{0,80}(?:contract|agreement|nda|legal form|form|disclosure|document)\b",
    r"\bfile .{0,80}(?:tax return|taxes|court document|court filing|lawsuit|legal document|legal filing)\b",
    r"\b(?:submit|file|update|change|cancel) .{0,80}(?:insurance claim|insurance coverage|insurance policy|claim)\b",
    r"\benroll .{0,80}(?:health plan|health insurance|benefits?|medical plan|insurance plan)\b",
    r"\b(?:change|update|submit|confirm) .{0,80}(?:benefits? election|benefits? enrollment|open enrollment|coverage election)\b",
    r"\b(?:refill|order) .{0,80}(?:prescription|prescription refill|medication|medicine)\b",
    r"\b(?:send|submit|upload) .{0,80}(?:medical form|medical record|health record|patient form|clinic form)\b",
    r"\b(?:renew|apply for|submit) .{0,80}(?:passport|visa|driver'?s license|state id|government id)\b",
    r"\bregister .{0,80}(?:to vote|voter registration)\b",
    r"\b(?:change|update) .{0,80}(?:mailing address|home address|residential address|address).{0,80}(?:dmv|government|irs|ssa|benefits?|insurance|bank|brokerage)\b",
    r"\b(?:change|update|add|remove) .{0,80}(?:emergency contact|beneficiary|dependent)\b",
    r"\b(?:turn|switch) (?:on|off) (?:notifications?|alerts?)\b",
    r"\bset (?:notifications?|alerts?) (?:on|off)\b",
    r"\b(?:enable|disable) (?:notifications?|alerts?)\b",
    r"\bsnooze (?:notifications?|alerts?)\b",
    r"\b(?:unbookmark|unfavorite|unstar|unwatch|unsave|unlike|unreact) .{0,80}(?:post|profile|item|result|listing|repo|repository|comment|message|video|photo)\b",
    r"\bremove .{0,80}(?:reaction|like|bookmark|favorite|star|watch|saved item|saved post).{0,80}(?:post|profile|item|result|listing|repo|repository|comment|message|video|photo)\b",
    r"\bstop watching .{0,80}(?:repo|repository|github|project)\b",
    r"\b(?:mute|report|hide) .{0,80}(?:profile|comment|reply|message|post|thread|user)\b",
    r"\bschedule .{0,80}(?:meeting|call|demo|appointment|interview|post|message|email|event)\b",
    r"\b(?:cancel|reschedule|update|move) .{0,80}(?:calendar event|event|meeting|appointment|reservation|booking)\b",
    r"\b(?:cancel|unschedule|reschedule) .{0,80}(?:scheduled )?(?:post|message|email|campaign|send|publish)\b",
    r"\bbook .{0,80}(?:appointment|meeting|call|demo|reservation|table|flight|hotel|ticket|room)\b",
    r"\bapply (?:for|to) .{0,80}(?:job|role|position|application|program|loan|grant|school|university|college)\b",
    r"\bremove .{0,80}(?:lead|prospect|contact|customer|person).{0,80} from .{0,80}(?:list|campaign|sequence|workflow|automation|pipeline|crm|salesforce|hubspot|pipedrive|zoho|apollo)\b",
    r"\b(?:unenroll|un-enroll|unsubscribe) .{0,80}(?:lead|prospect|contact|customer|person).{0,80}(?:campaign|sequence|workflow|automation|list)\b",
    r"(?:^|[.;,]\s*|\b(?:and|then|also|please|to)\s+)request (?:info|information|(?:a )?demo|(?:a )?quote|pricing|(?:a )?consultation)\b",
)
EMAIL_EXTERNAL_WRITE_PATTERNS = (
    r"(?:^|[.;,]\s*|\b(?:and|then|also|please|to)\s+)email (?:this|that|these|those|the|a|an|my|our|all|each|every|selected|qualified|warm|lead|leads|prospect|prospects|customer|customers|client|clients|contact|contacts|candidate|candidates|person|people|user|users|vendor|vendors|owner|owners|admin|admins|team|teams|company|companies|recipient|recipients|them|him|her)\b",
    r"(?:^|[.;,]\s*|\b(?:and|then|also|please|to)\s+)email [a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b",
)
UI_WRITE_ACTIONS = (
    "accept",
    "activate",
    "apply",
    "approve",
    "attend",
    "bid",
    "block",
    "book",
    "bookmark",
    "buy",
    "checkout",
    "comment",
    "confirm",
    "connect",
    "decline",
    "dm",
    "donate",
    "follow",
    "fork",
    "going",
    "heart",
    "invite",
    "like",
    "message",
    "mute",
    "pin",
    "post",
    "publish",
    "purchase",
    "react",
    "reply",
    "report",
    "request",
    "reserve",
    "respond",
    "retweet",
    "repost",
    "rsvp",
    "save",
    "schedule",
    "send",
    "share",
    "star",
    "submit",
    "subscribe",
    "tag",
    "upload",
    "upvote",
    "vote",
    "watch",
)
UI_WRITE_ACTION_PATTERN = "|".join(UI_WRITE_ACTIONS)
EXTERNAL_WRITE_UI_ACTION_PATTERNS = (
    rf"\b(?:click|tap|press|hit|select|activate)\b(?:\s+(?:on|the|this|that|a|an|final|primary|main|blue|green))*\s+(?:{UI_WRITE_ACTION_PATTERN})(?:\s+(?:button|icon|link|control|action|cta))?\b",
    r"\b(?:press|hit)\s+enter\s+to\s+(?:send|submit|publish|post|comment|reply|respond|dm|message)\b",
)
FOLLOWUP_EXTERNAL_WRITE_ACTION_PATTERN = "|".join(re.escape(term) for term in sorted(EXTERNAL_WRITE_KEYWORDS, key=len, reverse=True))
FOLLOWUP_EXTERNAL_WRITE_PATTERNS = (
    rf"(?:^|[.;,]\s*|\b(?:and|then|also|please|to)\s+)(?:{FOLLOWUP_EXTERNAL_WRITE_ACTION_PATTERN})(?![a-z0-9-])",
)
READ_ONLY_CONTENT_LOOKUP_PATTERNS = (
    r"^\s*(?:browse|collect|extract|find|inspect|list|monitor|read|review|scan|scrape|search|summarize|summarise|analyze|analyse)\b.*\b(?:post|posts|comment|comments|message|messages|reply|replies|dm|dms)\b",
)
READ_ONLY_REFERENCE_PATTERNS = (
    r"^\s*(?:browse|collect|extract|find|inspect|list|read|review|scan|scrape|search|summarize|summarise|analyze|analyse)\b.*\b(?:public|docs?|documentation|help|guide|policy|best practices|examples|article|blog|page)\b",
    r"\b(?:create|write|save) local notes about\b",
)
NON_EXTERNAL_CONTENT_PLANNING_PATTERNS = (
    r"\bposting schedule (?:outline|template|draft|plan)\b",
)
LOCAL_OUTPUT_OBJECT_PATTERNS = (
    r"\b(?:create|write|add|append|save|export) .{0,100}(?:lead|leads|prospect|prospects|contact|contacts|customer|customers).{0,100}(?:local|locally|local output|local csv|csv file|output file|json file|local file|artifact|artifacts)\b",
    r"\b(?:create|write|add|append|save|export) .{0,100}(?:local|locally|local output|local csv|csv file|output file|json file|local file|artifact|artifacts).{0,100}(?:lead|leads|prospect|prospects|contact|contacts|customer|customers)\b",
)
POSITIVE_CRM_DESTINATION_PATTERNS = (
    r"\b(?:crm|salesforce|hubspot|pipedrive|zoho|apollo)\b",
)
PUBLIC_SEARCH_SUBMISSION_PATTERNS = (
    r"\bsubmit .{0,60}(?:public|site|website|page|visible)?\s*(?:search|filter|sort)(?:\s+(?:form|query|box|field))?\b",
    r"\bpress (?:enter|return) to submit .{0,60}(?:search|filter|sort)\s+(?:form|query|box|field)\b",
)
UNSAFE_FORM_SUBMISSION_CONTEXT_PATTERNS = (
    r"\b(?:lead|contact|application|checkout|signup|sign-up|registration|payment|order|purchase|quote|demo|pricing|consultation|message|comment|reply|review|poll|booking|appointment|reservation|subscribe|unsubscribe|upload|file)\s+form\b",
    r"\bform .{0,60}(?:lead|contact|application|checkout|signup|sign-up|registration|payment|order|purchase|quote|demo|pricing|consultation|message|comment|reply|review|poll|booking|appointment|reservation|subscribe|unsubscribe|upload|file)\b",
)
EXPLICIT_WRITE_ACTION_SEGMENT_PATTERNS = (
    r"(?:^|[.;,]\s*|\b(?:and|then|also|please|to)\s+)(?:post|comment|reply|respond|message|dm|send|publish)\b",
)
LOCAL_DELIVERY_REQUEST_PATTERNS = (
    r"^\s*send (?:me|us) (?:a |an |the )?(?:summary|report|result|results|list|notes|analysis|answer|findings)\b",
)
NON_DRAFTABLE_WRITE_PATTERNS = EXTERNAL_WRITE_PATTERNS


def infer_risk(goal: str) -> RiskLevel:
    text = goal.lower()
    if _contains_destructive_action(text):
        return "destructive"
    if _is_draft_only_text_workflow(text):
        return "credential" if _contains_credentials(text) else "mutating"
    if _contains_external_write(text):
        return "external_write"
    if _contains_credentials(text):
        return "credential"
    if _is_read_only_reference_request(text) or _is_read_only_content_lookup(text):
        return "read"
    if any(word in text for word in ("fill", "click", "type", "edit", "change")):
        return "mutating"
    return "read"


def enrich_policy_flags(task: TaskSpec) -> TaskSpec:
    text = task.goal.lower()
    risk = infer_risk(task.goal)
    task.draft_only = draft_only_for_goal(task.goal)
    if _contains_external_write(text) and not task.draft_only:
        task.external_write = True
    if risk == "destructive":
        task.external_write = True
    if requires_auth_for_goal(task.goal):
        task.requires_auth = True
    if long_running_for_goal(task.goal):
        task.long_running = True
    return task


def approval_required(task: TaskSpec) -> bool:
    risk = infer_risk(task.goal)
    return task.external_write or task.target_scope in {"private_network", "link_local", "local_file"} or risk in ("external_write", "credential", "destructive")


def requires_auth_for_goal(goal: str) -> bool:
    return _contains_credentials(goal.lower())


def draft_only_for_goal(goal: str) -> bool:
    return _is_draft_only_text_workflow(goal.lower())


def long_running_for_goal(goal: str) -> bool:
    return _contains_any(goal.lower(), LONG_RUNNING_KEYWORDS)


def _contains_destructive_action(text: str) -> bool:
    return _contains_any(
        text,
        (
            "delete",
            "destroy",
            "reset account",
            "cancel subscription",
            "deactivate account",
            "disable account",
            "close account",
        ),
    )


def _contains_external_write(text: str) -> bool:
    if _is_local_delivery_request(text):
        return False
    if _is_safe_public_search_submission(text):
        return False
    if _is_local_output_object_request(text):
        return False
    if _is_non_external_content_planning_request(text):
        return False
    if _is_read_only_reference_request(text):
        return False
    if _is_read_only_content_lookup(text):
        return False
    return (
        _contains_any(text, EXTERNAL_WRITE_KEYWORDS)
        or _contains_any_pattern(text, EXTERNAL_WRITE_PATTERNS)
        or _contains_any_pattern(text, EMAIL_EXTERNAL_WRITE_PATTERNS)
        or _contains_any_pattern(text, EXTERNAL_WRITE_UI_ACTION_PATTERNS)
    )


def _contains_credentials(text: str) -> bool:
    if _is_read_only_reference_request(text):
        return False
    return _contains_any(text, CREDENTIAL_KEYWORDS) or _contains_any_pattern(text, CREDENTIAL_PATTERNS)


def _is_read_only_content_lookup(text: str) -> bool:
    return _contains_any_pattern(text, READ_ONLY_CONTENT_LOOKUP_PATTERNS) and not (
        _contains_any_pattern(text, EXPLICIT_WRITE_ACTION_SEGMENT_PATTERNS)
        or _contains_any_pattern(text, EXTERNAL_WRITE_PATTERNS)
        or _contains_any_pattern(text, EXTERNAL_WRITE_UI_ACTION_PATTERNS)
        or _contains_any(text, NON_DRAFTABLE_WRITE_KEYWORDS)
        or _contains_any_pattern(text, EMAIL_EXTERNAL_WRITE_PATTERNS)
    )


def _is_read_only_reference_request(text: str) -> bool:
    return _contains_any_pattern(text, READ_ONLY_REFERENCE_PATTERNS) and not _contains_followup_external_write_signal(text)


def _is_non_external_content_planning_request(text: str) -> bool:
    return _contains_any_pattern(text, NON_EXTERNAL_CONTENT_PLANNING_PATTERNS) and not (
        _contains_any_pattern(text, EXPLICIT_WRITE_ACTION_SEGMENT_PATTERNS)
        or _contains_any_pattern(text, EMAIL_EXTERNAL_WRITE_PATTERNS)
        or _contains_any_pattern(text, EXTERNAL_WRITE_UI_ACTION_PATTERNS)
    )


def _is_local_output_object_request(text: str) -> bool:
    if not _contains_any_pattern(text, LOCAL_OUTPUT_OBJECT_PATTERNS):
        return False
    normalized = re.sub(
        r"\bnot\s+(?:in|to|into)\s+(?:crm|salesforce|hubspot|pipedrive|zoho|apollo)\b",
        "",
        text,
    )
    return not _contains_any_pattern(normalized, POSITIVE_CRM_DESTINATION_PATTERNS)


def _is_safe_public_search_submission(text: str) -> bool:
    if not _contains_any_pattern(text, PUBLIC_SEARCH_SUBMISSION_PATTERNS):
        return False
    remainder = _without_pattern_matches(text, PUBLIC_SEARCH_SUBMISSION_PATTERNS)
    return not (
        _contains_credentials(text)
        or _contains_any_pattern(text, UNSAFE_FORM_SUBMISSION_CONTEXT_PATTERNS)
        or _contains_any_pattern(remainder, EMAIL_EXTERNAL_WRITE_PATTERNS)
        or _contains_any_pattern(remainder, EXPLICIT_WRITE_ACTION_SEGMENT_PATTERNS)
        or _contains_any_pattern(remainder, EXTERNAL_WRITE_PATTERNS)
        or _contains_any_pattern(remainder, EXTERNAL_WRITE_UI_ACTION_PATTERNS)
        or _contains_any(remainder, NON_DRAFTABLE_WRITE_KEYWORDS)
    )


def _without_pattern_matches(text: str, patterns: tuple[str, ...]) -> str:
    stripped = text
    for pattern in patterns:
        stripped = re.sub(pattern, " ", stripped)
    return stripped


def _is_local_delivery_request(text: str) -> bool:
    if not _contains_any_pattern(text, LOCAL_DELIVERY_REQUEST_PATTERNS):
        return False
    remainder = _local_delivery_remainder(text)
    return not _contains_followup_external_write_signal(remainder)


def _local_delivery_remainder(text: str) -> str:
    for pattern in LOCAL_DELIVERY_REQUEST_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return text[match.end() :]
    return ""


def _contains_followup_external_write_signal(text: str) -> bool:
    return (
        _contains_any_pattern(text, FOLLOWUP_EXTERNAL_WRITE_PATTERNS)
        or _contains_any_pattern(text, EXTERNAL_WRITE_PATTERNS)
        or _contains_any_pattern(text, EMAIL_EXTERNAL_WRITE_PATTERNS)
        or _contains_any_pattern(text, EXTERNAL_WRITE_UI_ACTION_PATTERNS)
    )


def _is_draft_only_text_workflow(text: str) -> bool:
    if _contains_any(text, NON_DRAFTABLE_WRITE_KEYWORDS) or _contains_any_pattern(text, NON_DRAFTABLE_WRITE_PATTERNS):
        return False
    return _contains_any(text, DRAFT_ONLY_KEYWORDS) and _contains_any(text, DRAFT_TEXT_KEYWORDS)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_term(text, term) for term in terms)


def _contains_term(text: str, term: str) -> bool:
    boundary = r"(?![a-z0-9])" if "-" in term else r"(?![a-z0-9-])"
    pattern = rf"(?<![a-z0-9]){re.escape(term)}{boundary}"
    return re.search(pattern, text) is not None


def _contains_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) is not None for pattern in patterns)
