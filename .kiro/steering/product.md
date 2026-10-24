# Product: GeneMatch

GeneMatch lets two partners find out whether their future children could inherit a recessive genetic disease, without either one ever seeing the other's DNA. Lab-certified carrier results are compared using private set intersection (PSI), and an AI counselor explains the outcome and next steps.

## Who it is for
- Couples planning a family who want carrier compatibility information without exposing their genetic data to each other, an app vendor, or a server.
- Built for the Hackachusetts Biotech & Healthcare track (Kiro, Oct 24 2026).

## What the user experiences
1. Each partner holds a lab-issued carrier credential in their wallet.
2. One partner starts a check and shares an invite code; the other joins and accepts the result mode.
3. Each sees only the agreed result: either the number of shared carrier genes, or the names of the shared conditions.
4. A counselor agent explains what the result means and produces questions to bring to a genetic counselor.
5. The same credential proves clinical-trial eligibility by sharing exactly one lab-signed finding.

## Product principles
- Nobody sees more than the agreed result: not the partner, not the relay server, not the AI agent.
- Inputs are lab-certified; you cannot invent carrier results.
- Plain language everywhere. The UI never assumes the user knows cryptography or genetics.
- GeneMatch is an education and screening-conversation tool, never a diagnosis. Always refer to a genetic counselor.

## Non-goals
- Real patient data (synthetic data only in this build).
- X-linked and dominant conditions (autosomal recessive only).
- Variant-level interpretation; matching is at the gene level.
