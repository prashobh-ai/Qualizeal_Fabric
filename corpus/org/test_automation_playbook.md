# QualiZeal Test Automation Playbook

This playbook is the learning material for testers and quality engineers who want to build or reuse automation. It explains where the automation assets live, how to find an existing script for a test case or suite before writing a new one, and the conventions every automation script follows.

## Find a script before you write one

Before automating a test case or suite, search the fabric for an existing script. Automation scripts are indexed by the case or suite they cover, the feature under test, and the framework they use. A tester can ask the knowledge fabric for "an automation script for <feature>" and get the matching scripts with a link to the exact file and lines.

## Conventions

Every automation script names the suite and the case it covers, keeps assertions close to the behaviour under test, and runs without manual setup. Data is generated, never hard-coded to a person or a date. Scripts are grouped into suites by feature area so a whole suite can run together.

## Frameworks

QualiZeal automation spans unit, integration and end-to-end suites. The reference frameworks are the standard test runners for each language. New suites should reuse the existing fixtures and helpers rather than duplicating setup.

## Coverage and gaps

The fabric tracks which suites exist and which features have no automation yet. A gap in coverage is a candidate for a new script; a feature that already has a script is a candidate for reuse.
