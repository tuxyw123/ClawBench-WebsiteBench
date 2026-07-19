# Limitations

- This is a narrow task replica, not a general Idealist mirror. Unrelated areas
  lead to a same-origin boundary page.
- Job fixtures are deterministic snapshots adapted from publicly visible
  Idealist listing semantics; dates and availability do not track the live site.
- Registration, sign-in, profile completion, resume handling, and application
  delivery are local simulations. The assigned resume is represented by its
  filename and profile metadata; no PDF bytes are uploaded.
- Search supports the task-relevant keyword, location, job type, and
  organization type controls. It does not implement geospatial ranking,
  pagination, recommendations, alerts, or live employer inventories.
- `SUBMITTED_LOCALLY` means a durable SQLite record only. There is no real
  Idealist account, employer delivery, email, upload, identity check, analytics,
  advertising, or third-party side effect.
