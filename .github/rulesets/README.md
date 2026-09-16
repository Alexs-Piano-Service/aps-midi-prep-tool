# Public repository rulesets

These definitions are ready to import into
`Alexs-Piano-Service/aps-midi-prep-tool`. Keeping JSON files in the repository
does not activate GitHub protection; they must be applied in repository settings
or through the API.

- `main.json` requires `Tests (ubuntu-latest)` and `Tests (windows-latest)` from
  GitHub Actions (app ID `15368`, confirmed against the public repository's CI).
  Pull requests must be up to date with `main`. Deletion and force pushes are
  blocked for users without bypass permission.
- `release-tags.json` restricts creation, updates, and deletion of `v*` tags to
  repository administrators.

Both rulesets are **Active** and explicitly permit repository administrators
(`RepositoryRole` ID `5`) to bypass them. Administrator direct pushes can still
bypass CI. Tag restrictions control who creates tags; they do not prove that an
administrator used `scripts/tag_release.py`. Continue using that helper and the
release workflow's independent CI gate.

## Apply

In the public repository's
[Settings → Rules → Rulesets](https://github.com/Alexs-Piano-Service/aps-midi-prep-tool/settings/rules),
choose **New ruleset → Import a ruleset** and import each JSON file. Check the
targets, required checks, and administrator bypass before saving.

Alternatively, with GitHub CLI authenticated using repository **Administration:
write** access, create the two rulesets:

```bash
gh api --method POST repos/Alexs-Piano-Service/aps-midi-prep-tool/rulesets \
  --input .github/rulesets/main.json
gh api --method POST repos/Alexs-Piano-Service/aps-midi-prep-tool/rulesets \
  --input .github/rulesets/release-tags.json
```

These commands create rulesets. If a ruleset with the same name already exists,
inspect it and use `PUT .../rulesets/ID` to update that ruleset instead of creating
a duplicate.

## Verify

List the resulting rulesets and inspect each returned ID to confirm active
enforcement, targets, rules, and bypass actors:

```bash
gh api repos/Alexs-Piano-Service/aps-midi-prep-tool/rulesets
gh api repos/Alexs-Piano-Service/aps-midi-prep-tool/rulesets/ID
gh api repos/Alexs-Piano-Service/aps-midi-prep-tool/rules/branches/main
```

The branch response should contain both required CI checks. These settings apply
only to the repository where they are imported.

References: [GitHub ruleset API](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset)
and [importing repository rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository#importing-prebuilt-rulesets).
