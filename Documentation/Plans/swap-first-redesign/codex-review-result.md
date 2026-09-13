**I would revise the detector before merging.** The Freejak fix works, and your diagnosis of the normal ALS geometry path is correct. However, “bass has ended” is being treated as “no fadeable content remains,” and the universal `interim_v1` no-change claim is too strong.

The supplied patch exactly matches `8913694`. No files were changed.

1. **[P2] The bass signal misclassifies audible percussion tails.**

   `bass_out_is_end` actually means `n_bars - bass_out <= 4`, as defined in [align_engine.py:634](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/align_engine.py:634>). It does not establish that other stems have ended. The new detector nevertheless returns `False` immediately after that bass-out point, overriding its remaining-duration check. [align_engine.py:774](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/align_engine.py:774>)

   **Real corpus example:** Christoph — *The Rise* has `n_bars=256`, bass-out ≈253, and an outro spanning bars 253–256 with `stems_on=["drums"]`. At swap bar 253, the detector reports no content despite three bars of percussion remaining.

   A constructed example also demonstrates the margin consequence: end=200, bass-out=197, swap=198.5, percussion continuing to 200. The detector returns `False`, authorizing a six-beat fade where the stated content-aware rule should retain the eight-beat requirement.

   **Recommendation:** treat bass-out as evidence about bass ownership, not sufficient evidence of a cold ending. Check non-bass activity, or explicitly describe this as a near-end heuristic rather than a content detector.

2. **[P2] The content flag can become stale after arrangement mutations.**

   It is computed before `plan_fill_or_cut`, then copied unchanged into the report. [align_engine.py:2344](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/align_engine.py:2344>)

   In the real Christoph → A Studio pair, the flag is `False` at bar 253, but the planner inserts five bars of outgoing-tail loops: the outgoing then continues to bar 261—**eight bars after the locked swap**. The arranger explicitly shifts the tail and extends its end. [propose_arrangement.py:816](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/propose_arrangement.py:816>)

   This example does **not** change the clamp outcome; both margins fit comfortably. It does demonstrate that the reported boolean cannot reliably mean “the arranged outgoing has nothing left after the swap.” Recompute against final playback geometry, or narrow the field’s meaning to its native-source estimate.

3. **The historical `interim_v1` evidence holds; the universal guarantee does not.**

   Independently verified:

   - The unchanged baseline passes, including its 380-pair and 113-pair rescue layers.
   - My 380-pair sweep applied the planner’s loop/cut geometry before comparing margins: **zero changed outcomes** among 265 completed pair plans; 113 pairs fail alignment and two fail fill/cut planning.
   - Held-out Side A produces identical old/new volume and bass automation points.

   But the change has no `interim_v1` exclusion. Its default `True` is overwritten during production position computation, and the margin consumer applies the flag regardless of transition policy. [apply_automation.py:692](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/apply_automation.py:692>)

   **Executed counterexample using `INTERIM_V1`:** an outgoing track ending at bar 200 with its final boundary at 199, paired with an incoming 32-bar intro, selects the legacy alignment with offset=167 and overlap=33 bars. No loops/cuts are planned.

   | Result | Parent commit | This commit |
   |---|---:|---:|
   | Final swap | 792 beats | 796 beats |
   | Outgoing fade | 792–800 | 796–800 |

   Both succeed. This exercises the supported no-landmarks branch of [align_pair](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/align_engine.py:1438>), which the historical corpus does not establish as unchanged.

   The defensible claim is **“no regression observed in the checked corpus and held-out Side A,”** not “cannot alter any `interim_v1` output.”

4. **MiniMax’s (b) is wrong for the normal generated-ALS path, but a fallback hazard exists.**

   `main()` reads the arranged ALS first and uses JSON only when that parse is empty. [apply_automation.py:1297](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/apply_automation.py:1297>) I independently confirmed Yellody’s final outro ends at **708**, including the extension.

   **Concrete fallback scenario:** the parser requires `<AudioClip Id="…" Time="…">` in that attribute order. [extract_sections_als.py:44](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/extract_sections_als.py:44>) Reordering those attributes in memory preserved valid XML but made all 84 clips disappear from its result. Automation would consequently use the potentially stale JSON.

   That warrants parser/fallback hardening, **not MiniMax’s proposed duplication of overlap geometry**. I found no normal generated tail-loop case requiring item (b).

5. **There is a two-stage-bass interaction worth guarding.**

   The existing two-stage rule checks whether the kill clears the overlap boundary, but never requires `kill_beat > swap`. [apply_automation.py:755](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/apply_automation.py:755>)

   In an executed planner-level fixture with overlap=672–800, swap=796, a long outgoing outro, and incoming build→drop at 720, the newly relaxed margin accepts the transition and enables **partial@796, kill@720**. Sorted automation kills early, then restores outgoing bass at 796 before partially cutting it. [apply_automation.py:1038](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Source/apply_automation.py:1038>)

   This ordering defect predates the commit; the new margin admits another case exposing it. I did not reproduce it in the held-out build. Add an explicit stage-order guard and an envelope-level regression.

The remaining points:

- **One bar is defensible as an explicit duration floor** in this four-beats-per-bar pipeline; tempo scaling is not necessary merely for consistency with a four-beat margin. It is not evidence of silence or absent content.
- **Report wiring is correct.** I exercised the real writer→JSON→reader path in memory: `False` survives, and an absent field defaults to `True`.
- **Style selection remains length-based.** Cold long overlaps still select `LONG_BLEND` and fade; short overlaps with content still select `QUICK_SWAP`. This is unchanged, but the patch should not imply it implements content-aware style selection.
- **The error message retains useful diagnostics.** Its fade-room sentence is inaccurate for a swap *before* `ov_start`; “outside the required arranged interval” covers both boundaries. Also, the new wording test is ineffective: it rejects `"can only mean"`, a phrase absent from the old exception text too. [test_outgoing_post_swap_content.py:155](<G:/Wired Masters Dropbox/Sam Wills/0.1---GIT HUB---/Automated DJ Mixes/Tests/test_outgoing_post_swap_content.py:155>)

_Validated: 15 tests passed; four golden-fixture tests skipped. Saved B/C ALS validation passed. Old/new differential probes reproduced the findings above. ✓_
===CODEX-ASK-DONE exit=0 nonce=a2aee7170574491dba307db845637ea5===
