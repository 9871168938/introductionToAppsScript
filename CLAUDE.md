# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Companion code for the free online course [Introduction To Apps Script](https://courses.benlcollins.com/p/apps-script-blastoff). Each file is the Google Apps Script (`.gs`-style, written as `.js`) code taught in one lesson video. This is teaching material, not a deployed application — there is no build, package manager, linter, or test suite, and none should be added unless the user asks.

## Repository structure

The repo is a flat list of numbered lesson files, no subdirectories:

- **Naming convention**: `NNN_IntroToAppsScript_<Topic>.js`, numbered in course order (`003` through `028`). Lesson numbers are not fully contiguous (the course has non-code lessons in between).
- **Files `003`–`019`**: standalone, self-contained lessons, each demonstrating one concept in isolation (macros, custom functions, variables, operators, arrays, objects, loops, the Spreadsheet service, reading/writing sheet data, custom menus, formulas, formatting, charts). Each can be read independently — reading one does not require reading the others.
- **Files `020`–`028` ("Project" lessons)**: a single cumulative mini-project (a "Coding Questionnaire" tool built on Google Forms + Sheets + Gmail). Each successive project file is a superset of the previous one plus that lesson's new code — e.g. `026_...Project7.js` contains everything from `020`–`025` plus its own additions. When editing or explaining a project lesson, check the file's actual content rather than assuming it matches an earlier project file; don't assume changes should propagate backward to earlier project files or forward automatically to later ones — each numbered file is a frozen snapshot of that lesson's end state and later files were hand-updated independently.
- **`README.md`**: course description and sign-up link.
- **`AppsScript_Intro copy.jpg`**: banner image referenced by the README.

## Working with this code

- This is Google Apps Script, not Node.js: globals like `SpreadsheetApp`, `FormApp`, `GmailApp`, `Charts`, `Logger`, and `console.log` come from the Apps Script runtime and won't resolve or run under plain Node. There's nothing to `npm install` and no local way to execute these files — they're meant to be pasted into the Apps Script editor bound to a Google Sheet/Form.
- Early lessons use `var`; from lesson `006` onward the code shifts to `const`/`let` and arrow functions, matching what's taught at that point in the course — match the style already used in the surrounding/target file rather than "modernizing" earlier lessons.
- Commented-out `console.log(...)` lines and inline example output comments (e.g. `// [ 'None', 'Apps Script' ]`) are intentionally left in place as teaching aids showing what a call prints. Preserve this pattern when adding similar demonstration code; don't strip commented-out debug logs as cleanup.
- Project files use a placeholder `FORM_ID = 'XXXXXXXXXXXXXXXXXXXXXXXXX'` that the learner is meant to replace with their own Google Form ID — don't treat this as a bug to fix.
- Functions named `onOpen()` are Apps Script simple triggers that Google calls automatically when the bound Sheet opens (used here to add custom menus); they're not invoked directly elsewhere in the file.
