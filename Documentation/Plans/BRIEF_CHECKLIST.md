# Builder brief checklist (from the 2026-11-25 self-audit)

Every brief to a builder (Sonnet subagent, Codex, MiniMax) states these four things. The lead checks them before sending; a brief that leaves one out is not sent.

1. **What exists at hook time.** For any hook, gate or step that reads a file: name the step that writes the file and show that it exists when the hook runs, checked against the real pipeline order (`Tools/run_mix_pipeline.sh`). The bounce gate hook was briefed to run before any bounce could exist.
2. **Coverage count.** For any feature that reads data, print how much of the real data has the field before building on it (keys: 2,516 of 13,175 credits; 8 of 9 Tech House tracks had none).
3. **Verdict matrix on real sets.** For anything that returns PASS, WARN or FAIL, list the expected verdict on at least six real sets, taken from blends Sam called tight or flammed, never interpolated. Three of six disagreed on first build of the thresholds.
4. **Adversarial inputs.** Name the inputs a reviewer will try to break it with (forged files, half-time tempos, mono, silence). Each numeric limit cites its observed real maximum and its source.

Review rule: gate or verdict code gets its LAST fix round re-reviewed by an executing reviewer (an Opus subagent that runs the code), not only read by the lead and not only by the reading reviewer.
5. **Credentials stay out of builder briefs (added 2026-11-25).** A brief never tells a builder or reviewer to read the credentials file or set the database URL. For a feature that needs live rows, the lead runs `playlist_builder.py sql`/`build` itself, saves the rows to a JSON file in the scratch folder, and the brief points the builder at that file (`--rows`). The pick manifest brief of 2026-11-25 broke this once: the builder set the read-only URL inside its shell (nothing printed); the harness refused the same instruction for the Opus reviewer.
