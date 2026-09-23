# Windows installation and release checks

The repository provides `Install_And_Run_VOOL.ps1` and the batch entrypoint for
Windows installation. Run the following checks on a disposable Windows host or VM;
they are procedures, not a claim that this checkout has passed native acceptance.

## Fresh host

```bat
Test_VOOL_Windows_Gauntlet.cmd -InstallProfile auto-recommended
```

The gauntlet covers installation, Windows regression tests, provider and hardware
probes, the optional local generation benchmark, package building, and optional
OpenClaw checks. It records results under `dist\windows-gauntlet`.

For an installed checkout or CI, omit installation and live generation:

```bat
Test_VOOL_Windows_Gauntlet.cmd -SkipInstall -SkipBenchmark -Json
```

To require configured OpenClaw integration:

```bat
Test_VOOL_Windows_Gauntlet.cmd -RequireOpenClaw
```

`-SkipPackageBuild` explicitly omits packaging; such a run does not establish
release readiness. Preserve the JSON report and inspect every failed stage.

## Stack handoff

The fast profile checks the configured sibling repository entrypoints:

```bat
Test_VOOL_Windows_Stack.cmd
```

The release profile also runs their installer and test paths:

```bat
Test_VOOL_Windows_Stack.cmd -Profile release
```

Outputs are retained under `dist\windows-stack-handoff`. A missing sibling is
reported by the script; running this gate does not authorize modifying it.

## Capability limits

Native Windows has no kernel filesystem sandbox backend in the shared command
runner. Executable tools requiring that boundary refuse execution; use WSL2/Linux
with usable bubblewrap for those workflows. This restriction does not establish
or revoke support for unrelated native UI and API capabilities.

See [Windows capability matrix](WINDOWS_CAPABILITY_MATRIX.md) for historical host
evidence and its source revisions. Verify installation, restart, model routing,
permission refusals, and package identity on the actual target before release.
