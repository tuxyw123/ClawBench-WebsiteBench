# Limitations and safety boundaries

- This is a task-scoped replica for source task 583, not a complete Freshdesk or Freshworks environment. It covers registration, a single free Sprout workspace, a local agent inbox, ticket creation, and adjacent ticket-management and recovery states.
- Freshworks currently promotes a 14-day trial while the source task explicitly names the historic free Sprout account. The replica preserves the source task's Sprout requirement and labels it free; it does not model plan migration, billing, trial conversion, or payment.
- The upstream task exposes only `POST freshdesk.com/api/_/tickets`, without a body matcher. Dev-115 therefore defines a deterministic nine-field body grounded in public Freshdesk ticket properties and numeric API values.
- Requester `1001`, Test Agent `2002`, and Support group `3001` are stable local fixture IDs. They are not production Freshdesk identifiers. The Test Agent account is pre-provisioned because the source asks for assignment to a test agent.
- Phone source `3` represents the agent portal's create-on-behalf-of-customer workflow. Email, portal, chat, social, and live phone ingestion are outside this task.
- Search, views, dashboard metrics, SLA labels, contact context, edit, resolve, and reopen are limited to the current local workspace and its task-scoped tickets. Automation, knowledge base, analytics, AI, omnichannel routing, and administration are represented only where a visible local boundary is useful.
- Authentication is bound to one browser session and the assigned Alex Green fixture. Verification code `246810` is displayed locally; no email is delivered. Passwords are one-way hashed for local comparison, but this replica is not an identity provider and must not hold real credentials.
- Team invitations, new-customer creation, help, marketplace, and Slack/Google/Stripe connections only record local boundary events. No OAuth, identity, email, CRM, payment, analytics, advertising, customer, team, or integration request leaves the origin.
- All durable effects are restricted to the selected local SQLite database. The local verifier reset endpoint requires an explicit header and exists only for deterministic development checks.
- The replica uses a strict same-origin content security policy. No runtime assets, fonts, scripts, images, or APIs are loaded from Freshdesk, Freshworks, or another host.
- Freshdesk and Freshworks names remain trademarks of their owner. Their use identifies the publicly observable task surface and does not imply affiliation or endorsement.
