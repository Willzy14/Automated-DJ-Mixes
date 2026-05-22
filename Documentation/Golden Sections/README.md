# Golden Sections

Blessed `Sections_V<N>.json` files from closed projects. Used by
`Source/regress_section_detection.py` to catch regressions when
`phrase_viz.py` is tuned for a new mix.

## How to bless a project

1. Confirm with Sam that the mix is final and the chops are correct.
2. Create a file `<ProjectName>__final.json` here with this shape:

   ```json
   {
     "project_dir": "Test Project/<ProjectName>",
     "blessed_at": "YYYY-MM-DD",
     "sections": {  ... contents of Sections_V<N>.json ... }
   }
   ```

3. The `project_dir` is relative to the repo root; the regress script
   resolves it. If you omit `project_dir` the script will try to find
   the project by name.

## How to run

```
python Source/regress_section_detection.py
```

Exits non-zero if any project's section detection has drifted by more
than 1 bar from the blessed values.
