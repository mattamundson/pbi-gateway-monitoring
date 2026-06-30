@{
    # Run the default rule set. These scripts are operational tooling, so we
    # relax a few rules that are noisy for interactive admin scripts.
    IncludeDefaultRules = $true

    ExcludeRules = @(
        # Write-Host is intentional here — these are interactive, colorized
        # operator scripts meant to be read by a human at a console, not piped.
        'PSAvoidUsingWriteHost',

        # The deployer interactively reads the SP secret via Get-Credential and
        # hands it to Register-ScheduledTask; this is by design (FPM requires
        # LogonType=Password). The secret is never stored in plaintext by us.
        'PSAvoidUsingPlainTextForPassword',
        'PSAvoidUsingConvertToSecureStringWithPlainText',
        'PSUsePSCredentialType'
    )
}
