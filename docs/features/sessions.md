# Sessions And BG Checks

This is the practical map for Jane's orientation session flow and the background-check queue that follows it.

The important idea: a session is not just a Discord message. It is database state, persistent buttons, grading state, BG routing state, Roblox scan state, and post-orientation side effects all tied into one slightly cursed but very useful machine.

## Where It Lives

- `cogs/staff/sessionCog.py`
- `features/staff/clockins/engine.py`
- `features/staff/clockins/orientationAdapter.py`
- `features/staff/sessions/service.py`
- `features/staff/sessions/sessionControls.py`
- `features/staff/sessions/views.py`
- `features/staff/sessions/bgRouting.py`
- `features/staff/sessions/bgQueueMessaging.py`
- `features/staff/sessions/bgQueueViews.py`
- `features/staff/sessions/bgCheckViews.py`
- `features/staff/sessions/bgScanPipeline.py`
- `features/staff/sessions/postActions.py`
- `features/staff/sessions/viewPolicy.py`

## Commands People Actually Touch

- `/bg-add`
  Adds a Discord user to the next BGC spreadsheet Jane creates after an orientation.

## Orientation Flow

Orientation sessions are hosted by John Clanker. Jane's own `/orientation` command was removed in the cutover. John owns the Discord session (join, grading, result import, Finish) and the results post. Jane still creates the BGC spreadsheet.

When John's host presses `Finish`, John calls Jane's orientation API:

- `POST /orientation/bgc-spreadsheet`
- header `X-API-TOKEN`, the same token as `/enterOrientation` (`JANE_ORIENTATION_API_TOKEN`)
- body: `requestId`, `guildId`, `hostId`, `passedUserIds`, and optionally `hostName`, plus `channelId` and `messageId` of John's session message. IDs may be strings or integers.

Jane does not have to be a member of the server the orientation ran in. `guildId` selects her config, RoVer lookups and the `/bg-add` queue. `hostName` is the host's server nickname, which she uses for the forum entry title when she cannot look the host up herself.

Jane answers `202` straight away and builds the sheet in the background through `routeExternalOrientationSpreadsheet` in `features/staff/sessions/bgSpreadsheetRouting.py`:

1. Pending `/bg-add` users are appended to the passing attendees.
2. The BGC template is copied and the rows are written. Jane does her own Roblox lookups.
3. The `/bg-add` users that made it onto the sheet are marked consumed.
4. The audit log is written, with John's host as requester and a link to John's session message.
5. The link is posted to `bgCheckChannelId` and the forum entries are created.

There is no row in `sessions` or `attendees` for a John-run orientation, so `/bg-add` users are consumed against session id `0` and no review buckets are assigned.

A repeated `requestId` answers `200` with `"status": "duplicate"` and does nothing. The list of seen ids is kept in memory only.

For this to work the API must be enabled (`JANE_ORIENTATION_API_ENABLED=1`) and reachable from John. The default bind is `127.0.0.1:24003`, which only works when both bots run on the same host.

If the sheet never appears, check Jane's log for `Orientation API` lines.

The session views, grading controls and `Finish` handling for Jane-hosted sessions are still in the codebase. They keep already-posted session messages working after a restart, but nothing starts a new Jane-hosted orientation.

## BG Queue Flow

Only passing attendees become BG candidates.

Users queued with `/bg-add` are not added to the live orientation review queue and do not cause a spreadsheet by themselves. They are appended to the next orientation BGC spreadsheet for that server, then marked consumed so they do not appear again on a later sheet. If an orientation has no passing attendees and Jane skips spreadsheet creation, the queued `/bg-add` users stay pending.

Jane routes each passing attendee into a review bucket with this priority:

1. Role routing from `bgMinorAgeRoleIds` and `bgMajorAgeRoleIds`.
2. ORBAT age group routing from `bgMinorAgeGroups` and `bgAdultAgeGroups`.
3. Fallback routing from `bgUnknownDefaultsToMinor`.

The two buckets are:

- `adult`
  Displayed as `+18`.

- `minor`
  Displayed as `-18`.

Queue message IDs are stored separately:

- `sessions.bgQueueMessageId`
- `sessions.bgQueueMinorMessageId`

## Review Controls

Reviewers can approve, reject, claim, inspect info, and open the next pending attendee. This is the part staff actually lives in during BG work.

Access is checked through `features/staff/sessions/viewPolicy.py`.

The main review permissions are:

- `moderatorRoleId`
- `bgReviewModeratorRoleId`
- `bgCheckMinorReviewRoleId`
- `bgCheckMinorReviewRoleIds`

For the minor review guild, `bgCheckMinorReviewRoleId` is the ping role and `bgCheckMinorReviewRoleIds` is the allowed-role list.

## What Approval Does

When a reviewer approves a candidate:

1. The attendee row is marked `APPROVED`.
2. Any active claim for that attendee is cleared.
3. If this is an orientation session, Jane removes the pending BG role.
4. If this is an orientation session, Jane can award the host point.
5. Jane refreshes the session and BG queue messages.
6. Jane may attempt Roblox group auto-accept.
7. Jane may DM the user with Roblox join instructions if auto-accept cannot complete.
8. Jane may apply recruitment orientation bonus work.

The recruitment bonus path is intentionally idempotent. It only touches recruitment logs that still have `passedOrientation = 0`, updates the review embed when it can find it, and syncs the extra points to the Recruitment ORBAT if the original recruitment log was already approved.

## What Rejection Does

When a reviewer rejects a candidate:

1. The attendee row is marked `REJECTED`.
2. Any active claim for that attendee is cleared.
3. Jane refreshes the session and BG queue messages.
4. Jane keeps the result visible in the BG summary.

## Persistent Views

Session and BG views are restored at startup by the runtime bootstrap path.

This matters after restarts. If a queue is still pending and the message IDs are still stored, Jane should reattach the buttons instead of leaving everyone staring at a decorative embed.

If buttons do not work after restart, check:

- the relevant message still exists
- the session is not already `FINISHED` or `CANCELED`
- the message ID columns are populated
- startup logs mention persistent view restore

## Stored State

The main tables are created in `db/sqlite.py`.

Session-level state lives in `sessions`. The fields people usually care about are:

- `sessionId`
- `guildId`
- `channelId`
- `messageId`
- `sessionType`
- `hostId`
- `passwordHash`
- `status`
- `gradingIndex`
- `bgQueueMessageId`
- `bgQueueMinorMessageId`

Attendee-level state lives in `attendees`. The useful bits are:

- `sessionId`
- `userId`
- `examGrade`
- `bgStatus`
- `bgReviewBucket`
- Roblox scan and join status fields
- credit and processing fields

## Config Checklist

Check these first when sessions or BG queues start acting haunted:

- `instructorRoleId`
- `newApplicantRoleId`
- `pendingBgRoleId`
- `bgCheckChannelId`
- `bgCheckAdultReviewGuildId`
- `bgCheckAdultReviewChannelId`
- `bgCheckMinorReviewGuildId`
- `bgCheckMinorReviewChannelId`
- `bgCheckMinorReviewRoleId`
- `bgCheckMinorReviewRoleIds`
- `bgMinorAgeRoleIds`
- `bgMajorAgeRoleIds`
- `bgMinorAgeGroups`
- `bgAdultAgeGroups`
- `bgUnknownDefaultsToMinor`
- `bgReviewModeratorRoleId`
- `moderatorRoleId`
- `trainingResultsChannelId`
- `recruitmentAutoDetectOrientation`
- `recruitmentPointsOrientationBonus`
- `recruitmentChannelId`
- `robloxGroupId`
- `robloxOpenCloudApiKey`
- `roverApiKey`

## Common Failures

- A user cannot clock in.
  Check whether they still have the New Applicant role if `newApplicantRoleId` is configured.

- The host cannot finish.
  Check that every attendee has a grade.

- BG queues do not post.
  Check adult and minor review channel IDs, Jane's channel permissions, and logs around `postBgQueue`.

- The wrong queue gets an attendee.
  Check role routing first, then ORBAT age group, then `bgUnknownDefaultsToMinor`.

- Review buttons deny a reviewer.
  Check the review guild, the reviewer roles, and `viewPolicy.py`.

- Roblox auto-accept fails.
  Check RoVer lookup, Open Cloud config, group ID, and whether the user actually requested to join the group.

- Recruitment orientation bonuses do not show up.
  Check `recruitmentAutoDetectOrientation`, the bonus point value, the recruitment review message location, RoVer lookup, and Recruitment ORBAT sheet logs.

## Safe Edit Rules

- Keep session DB changes backward-compatible.
- Do not remove persistent view custom IDs without a migration plan.
- Treat `Finish` as a high-risk path because it posts queues, posts results, changes session status, and triggers side effects.
- Be careful with role and age routing changes because they decide whether users are sent to `+18` or `-18` review.
- Prefer small targeted changes and smoke-test with a fake session in a test server.
