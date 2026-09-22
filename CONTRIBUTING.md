# Contributing

Use a short-lived feature branch and submit changes through a pull request.
Keep each pull request focused on one behavior or migration step, explain the
validation performed, and identify any test that was not run.

Do not commit generated outputs, benchmark corpora, passenger or business
data, model weights, third-party toolchains, or prebuilt binaries. Formal24,
training, and long-running experiments are manual workflows and are not normal
pull-request checks.

For solver changes, preserve legality before comparing objective values. Use
"current best-known integer reference" where applicable; do not describe it as
a global optimum. Do not report proxy or research results as production gains.

