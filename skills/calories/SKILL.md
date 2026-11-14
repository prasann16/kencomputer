---
name: calories
description: Track what they eat like a calorie app: every meal from a photo or a few words logged with calories, protein, carbs and fat, the day's totals against their targets, their usual meals remembered, daily or weekly summaries and charts, and reminders only if they want them. Use when they ask to track calories, food, macros, a diet or weight, send a meal photo to log, or ask how they're doing against a goal.
---

# Calorie tracking

It works like a calorie app (Cal AI, MyFitnessPal), without the app. They send a photo or a few words ("2 eggs and
toast"), you log every item with its calories, protein, carbs and fat, and tell them where the day stands. Every meal
they mention is logged, always: they should be able to ask any day "what did I eat?" and get the full picture. Everything lives in `calories/` in your workspace, so every
total, summary and chart reads from the same place and nothing is lost between chats.

## The files

- `calories/log.csv`: one row per food eaten, never rewritten, only added to (a correction is a new row that says
  so, or an edit of that one row when they correct it straight away). Columns:
  `date,time,meal,food,kcal,protein_g,carbs_g,fat_g,how,note`
  - `date` YYYY-MM-DD and `time` HH:MM in their time zone; `meal` breakfast, lunch, dinner, snack or drink.
  - `kcal`, `protein_g`, `carbs_g`, `fat_g`: always all four, one number each, for every row.
  - `how`: `photo`, `told` (they said it), `usual` (one of their usual meals), or `label` (from a packet or menu).
- `calories/profile.md`: their daily targets for calories, protein, carbs and fat, why (losing, gaining, keeping
  steady), units, and which reminders or summaries they said yes to. When they start, ask once for their goal (and,
  if they want targets worked out, height, weight, age, sex and how active they are); set targets from that, or use
  the numbers they give. No goal is fine: then you report what they ate without a target.
- `calories/usuals.md`: their regular things with their numbers ("oat latte, large: 180 kcal, 6 g protein"). When
  they correct you or tell you a number, save it here and use it every time after.

Read `profile.md` and `usuals.md` before logging, and check today's rows in `log.csv` before saying a total.

## Logging a meal

- **A photo:** name each item and the portion you're assuming, then its numbers. A photo can't show oil, sauce,
  milk type or what's under the top layer: guess sensibly, and ask one short question only when the answer moves the
  number a lot (whole vs skimmed milk in a big latte, a dressing on a salad). Never quiz them about every item.
- **Words:** "a chicken wrap and a coke" is enough. Use `usuals.md` first, then typical portions.
- **A label or menu:** if they send a packet, a menu with numbers, or a recipe, use those numbers (`how` = `label`).
- **After each log:** the meal's calories, protein, carbs and fat, then the day so far against their targets (what's
  left), in a few short lines. Give single numbers, like a calorie app does, not ranges. Logging should feel like a
  text back, not a report.
- **Getting it right:** use their usuals and any label first, typical portions second. If they correct a number, fix
  that row and remember it. Numbers from a photo can't be exact: if they mention an eating disorder, a medical diet or
  medication that depends on exact numbers, say plainly that photo tracking isn't precise enough for that and suggest
  their doctor or a dietitian.
- **Late or missed meals:** log yesterday's when they tell you ("forgot to log dinner yesterday, pizza"), with that
  date. Don't invent meals they didn't mention.

## Summaries, charts and reminders

- **Only what they asked for.** When they start, offer once: a short total each evening, a weekly look back, or a
  nudge if a usual meal hasn't been logged. Set up what they say yes to as jobs in your schedule (jobs.json), note
  it in `profile.md`, and change or remove it the moment they ask. No reminders they didn't choose.
- **A day or a week:** calories, protein, carbs and fat, each against its target. Totals and averages come from
  `log.csv` (compute them with a quick script rather than adding
  in your head). Mention a pattern only when it's real and useful ("weekends run about 600 over").
- **Charts:** when a picture helps (a week or a month of days against their goal, protein over time), make one from
  `log.csv` with a short script (matplotlib is fine), save it as a PNG and send it. Readable on a phone. How it looks
  is your call each time.
- **Weight:** if they share weights, keep them in `calories/weight.csv` (`date,weight,unit`) and chart them next to
  the calories when they ask.

## Photos from their phone

If the photos tools are there (`photos_find`) and they've said yes to it, you can pick up meal photos they shared
from their phone instead of them sending each one: log the ones that are clearly food, skip anything you're unsure
about, and tell them what you logged. Never go looking through their photos for meals unless they asked you to.
