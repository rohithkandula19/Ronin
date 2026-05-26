# 5-day launch plan

The order matters. Don't fire all of these at once.

## Day 0 — today

- [ ] **Generate a PyPI API token** at https://pypi.org/manage/account/token/ (scope: "Entire account" for the first release; tighten to project-scoped after).
- [ ] **Add `PYPI_TOKEN` as a GitHub Actions secret**: `Settings → Secrets and variables → Actions → New repository secret`.
- [ ] **Run the release**: `scripts/release.sh 0.2.0` — bumps, tags, pushes, watches CI.
- [ ] Once PyPI shows `ro-claude-kit-cli 0.2.0`, **test the install from a fresh shell**:
  ```bash
  curl -sSL https://raw.githubusercontent.com/rohithkandula19/Ronin/main/install.sh | bash
  csk init --demo -y
  csk briefing
  ```
  If anything errors here, *stop and fix* — every message below points at this install command.
- [ ] **Record the GIF**: `brew install vhs && vhs scripts/demo.tape` → drop the generated `demo.gif` into `scripts/` and reference it in the README.
- [ ] After all of that, do nothing else today. Let the artifact settle.

## Day 1 — Anthropic, privately

- [ ] Send `01_anthropic_email.md` to the Applied AI Engineer (Startups) recruiter / hiring manager.
- [ ] If you have warm intros to Anthropic technical staff: send `02_anthropic_dm.md` to two of them. Pick people whose work you actually engage with on Twitter.
- [ ] **Do not post publicly today.** You want Anthropic to see this with low context noise.

## Day 2 — founder friends, privately

- [ ] Send `03_founder_dm.md` to **three** specific founder friends with Stripe + Linear. Not a group blast.
- [ ] Schedule a 10-min screenshare with at least one of them. Watch them run `csk briefing` on their real data. Take notes.
- [ ] Update the README's "real-world feedback" section if anything surprising surfaces.

## Day 3 — Show HN

Submit Tuesday, Wednesday, or Thursday between 8–10am ET. Avoid weekends and Mondays.

- [ ] Be at your computer for **90 minutes after submitting**.
- [ ] Submit using `04_show_hn_title.txt` as the title and the repo URL.
- [ ] **Within 30 seconds**, post `05_show_hn_first_comment.md` as the first comment.
- [ ] Reply to every early commenter within 5 minutes. The first 30 minutes determine whether you hit the front page.
- [ ] Do not ask anyone to upvote. HN detects vote rings. Telling a friend "hey I submitted at 9am, here's the link in 10 min" is fine — explicit upvote asks are not.

## Day 4 — broader public

Pick one of these based on Day 3's outcome:

- **If Show HN got traction (>30 upvotes, front page time):**
  - [ ] Post `09_anthropic_public.md` on Twitter (the tagged version). Anthropic team will see it organically; you have HN as social proof.
  - [ ] Post `06_indie_hackers.md` on Indie Hackers + r/SideProject. Same content, different audiences.

- **If Show HN got buried (<10 upvotes):**
  - [ ] Skip the Anthropic public post. Don't tie the link to a launch that flopped.
  - [ ] Post `06_indie_hackers.md` anyway — different audience, often better reception.

## Day 5 — Twitter thread

- [ ] Post Variant A from `07_tweets.md` with the GIF/screenshot. Single thread, 3-4 tweets.
- [ ] Post `08_linkedin.md` on LinkedIn (different audience again — founders, recruiters, ex-colleagues).
- [ ] If you got real users from Days 2-3, **quote-tweet their reactions**. Social proof from anyone who isn't you outweighs every line you'd write.

## After Day 5

- [ ] Three messages to people who replied. Specific questions: *"what's the briefing missing for you?"* Three real users is worth 300 stars.
- [ ] If at least one founder uses it a second week — that's product-market signal. Build whatever they asked for next.
- [ ] If nobody uses it twice — that's also signal. Don't ship a v0.3 of a thing nobody wants. Either pivot the killer command or move on.

## Hard rules

1. **Don't apologize for the project in any post.** "Just a side project," "rough around the edges," "I'm not a designer" — all banned.
2. **Don't bundle multiple things.** One launch = one killer command. The kitchen sink is for the README's lower sections.
3. **Don't argue with HN trolls.** Reply once, factually, to substantive critiques. Ignore the rest.
4. **Don't DM the Anthropic team after sending the public post.** They've already seen it.
