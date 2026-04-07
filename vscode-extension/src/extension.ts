import * as vscode from 'vscode';
import * as path from 'path';
import * as cp from 'child_process';

const SECRET_KEY = 'googleAdsMcp.developerToken';
const MCP_SERVER_ID = 'google-ads-mcp';

// ---------------------------------------------------------------------------
// Activation
// ---------------------------------------------------------------------------

export async function activate(context: vscode.ExtensionContext) {
    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBar.command = 'googleAdsMcp.showStatus';
    context.subscriptions.push(statusBar);

    const refreshStatus = async () => {
        const ready = await isConfigured(context);
        statusBar.text = ready ? '$(check) Google Ads MCP' : '$(warning) Google Ads MCP';
        statusBar.tooltip = ready
            ? 'Google Ads MCP is configured. Click for status.'
            : 'Google Ads MCP not configured. Click for status.';
        statusBar.show();
    };

    context.subscriptions.push(
        vscode.commands.registerCommand('googleAdsMcp.setup', async () => {
            await runSetupWizard(context);
            await refreshStatus();
        }),
        vscode.commands.registerCommand('googleAdsMcp.installDeps', () =>
            installDependencies(context)
        ),
        vscode.commands.registerCommand('googleAdsMcp.showStatus', () =>
            showStatus(context)
        )
    );

    await refreshStatus();

    if (!(await isConfigured(context))) {
        const action = await vscode.window.showInformationMessage(
            'Google Ads MCP: Credentials are not configured yet.',
            'Setup Now',
            'Dismiss'
        );
        if (action === 'Setup Now') {
            await vscode.commands.executeCommand('googleAdsMcp.setup');
        }
    }
}

export function deactivate() {}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function isConfigured(context: vscode.ExtensionContext): Promise<boolean> {
    const cfg = vscode.workspace.getConfiguration('googleAdsMcp');
    const oauthPath = cfg.get<string>('oauthConfigPath');
    const token = await context.secrets.get(SECRET_KEY);
    return !!(oauthPath && token);
}

// ---------------------------------------------------------------------------
// Setup wizard — collects all three fields with native VS Code UI
// ---------------------------------------------------------------------------

async function runSetupWizard(context: vscode.ExtensionContext) {
    // ── Step 1: OAuth JSON file ──────────────────────────────────────────────
    const cfg = vscode.workspace.getConfiguration('googleAdsMcp');
    const currentOauthPath = cfg.get<string>('oauthConfigPath') || '';

    const picked = await vscode.window.showOpenDialog({
        canSelectFiles: true,
        canSelectFolders: false,
        canSelectMany: false,
        filters: { 'JSON files': ['json'], 'All files': ['*'] },
        title: 'Select your OAuth 2.0 Client Secret JSON',
        openLabel: 'Select',
        defaultUri: currentOauthPath
            ? vscode.Uri.file(path.dirname(currentOauthPath))
            : undefined,
    });

    if (!picked || picked.length === 0) {
        vscode.window.showWarningMessage('Google Ads MCP setup cancelled.');
        return;
    }
    const oauthConfigPath = picked[0].fsPath;

    // ── Step 2: Developer token (masked input) ───────────────────────────────
    const existingToken = await context.secrets.get(SECRET_KEY);
    const developerToken = await vscode.window.showInputBox({
        title: 'Google Ads Developer Token',
        prompt: 'Paste your Google Ads Developer Token',
        placeHolder: 'Found in Google Ads → Tools → API Centre',
        password: true,
        value: existingToken ?? '',
        ignoreFocusOut: true,
        validateInput: (v) => (v.trim() ? null : 'Developer token cannot be empty.'),
    });

    if (developerToken === undefined) {
        vscode.window.showWarningMessage('Google Ads MCP setup cancelled.');
        return;
    }

    // ── Step 3: Manager account ID (optional) ────────────────────────────────
    const currentMcc = cfg.get<string>('loginCustomerId') || '';
    const loginCustomerId = await vscode.window.showInputBox({
        title: 'Manager Account ID (optional)',
        prompt: 'Enter your Manager (MCC) account ID, or leave blank',
        placeHolder: 'e.g. 1234567890',
        value: currentMcc,
        ignoreFocusOut: true,
        validateInput: (v) => {
            if (!v.trim()) return null; // blank is fine
            return /^\d{10}$/.test(v.replace(/-/g, ''))
                ? null
                : 'Should be a 10-digit account ID (dashes OK).';
        },
    });

    if (loginCustomerId === undefined) {
        vscode.window.showWarningMessage('Google Ads MCP setup cancelled.');
        return;
    }

    // ── Save ─────────────────────────────────────────────────────────────────
    await cfg.update('oauthConfigPath', oauthConfigPath, vscode.ConfigurationTarget.Global);
    await context.secrets.store(SECRET_KEY, developerToken.trim());
    await cfg.update(
        'loginCustomerId',
        loginCustomerId.trim(),
        vscode.ConfigurationTarget.Global
    );

    // ── Write MCP server config into VS Code user settings ───────────────────
    await writeMcpConfig(context, oauthConfigPath, developerToken.trim(), loginCustomerId.trim());

    // ── Offer to install Python deps ─────────────────────────────────────────
    const action = await vscode.window.showInformationMessage(
        'Google Ads MCP configured! Install Python dependencies now?',
        'Install Dependencies',
        'Skip',
        'Reload VS Code'
    );

    if (action === 'Install Dependencies') {
        await installDependencies(context);
    } else if (action === 'Reload VS Code') {
        vscode.commands.executeCommand('workbench.action.reloadWindow');
    }
}

// ---------------------------------------------------------------------------
// Write the MCP server entry into VS Code user settings
// ---------------------------------------------------------------------------

async function writeMcpConfig(
    context: vscode.ExtensionContext,
    oauthConfigPath: string,
    developerToken: string,
    loginCustomerId: string
) {
    const cfg = vscode.workspace.getConfiguration('googleAdsMcp');
    const pythonPath = cfg.get<string>('pythonPath') || 'python3';
    const serverScript = path.join(context.extensionPath, 'python', 'server.py');

    const env: Record<string, string> = {
        GOOGLE_ADS_OAUTH_CONFIG_PATH: oauthConfigPath,
        GOOGLE_ADS_DEVELOPER_TOKEN: developerToken,
    };
    if (loginCustomerId) {
        env['GOOGLE_ADS_LOGIN_CUSTOMER_ID'] = loginCustomerId;
    }

    const serverEntry = {
        type: 'stdio',
        command: pythonPath,
        args: [serverScript],
        env,
    };

    // VS Code MCP servers live under the "mcp" configuration key
    const mcpCfg = vscode.workspace.getConfiguration('mcp');
    const servers: Record<string, unknown> = mcpCfg.get('servers') ?? {};
    servers[MCP_SERVER_ID] = serverEntry;
    await mcpCfg.update('servers', servers, vscode.ConfigurationTarget.Global);

    vscode.window.showInformationMessage(
        `MCP server "${MCP_SERVER_ID}" registered in VS Code settings.`
    );
}

// ---------------------------------------------------------------------------
// Install Python dependencies
// ---------------------------------------------------------------------------

function installDependencies(context: vscode.ExtensionContext): Promise<void> {
    return new Promise((resolve) => {
        const cfg = vscode.workspace.getConfiguration('googleAdsMcp');
        const pythonPath = cfg.get<string>('pythonPath') || 'python3';
        const requirementsPath = path.join(
            context.extensionPath,
            'python',
            'requirements.txt'
        );

        vscode.window.withProgress(
            {
                location: vscode.ProgressLocation.Notification,
                title: 'Google Ads MCP: Installing Python dependencies…',
                cancellable: false,
            },
            () =>
                new Promise<void>((done) => {
                    const proc = cp.spawn(
                        pythonPath,
                        ['-m', 'pip', 'install', '-r', requirementsPath],
                        { shell: false }
                    );

                    let stderr = '';
                    proc.stderr.on('data', (d: Buffer) => (stderr += d.toString()));

                    proc.on('close', (code) => {
                        if (code === 0) {
                            vscode.window.showInformationMessage(
                                'Google Ads MCP: Python dependencies installed successfully.'
                            );
                        } else {
                            vscode.window.showErrorMessage(
                                `pip install failed (exit ${code}).\n${stderr}\n\nTry running manually:\n${pythonPath} -m pip install -r "${requirementsPath}"`
                            );
                        }
                        done();
                        resolve();
                    });

                    proc.on('error', (err) => {
                        vscode.window.showErrorMessage(
                            `Could not run ${pythonPath}: ${err.message}\n` +
                            `Set "googleAdsMcp.pythonPath" to the correct interpreter.`
                        );
                        done();
                        resolve();
                    });
                })
        );
    });
}

// ---------------------------------------------------------------------------
// Status panel
// ---------------------------------------------------------------------------

async function showStatus(context: vscode.ExtensionContext) {
    const cfg = vscode.workspace.getConfiguration('googleAdsMcp');
    const oauthPath = cfg.get<string>('oauthConfigPath') || '(not set)';
    const loginCustomerId = cfg.get<string>('loginCustomerId') || '(not set)';
    const pythonPath = cfg.get<string>('pythonPath') || 'python3';
    const tokenStored = !!(await context.secrets.get(SECRET_KEY));

    const lines = [
        `**OAuth Config Path:** ${oauthPath}`,
        `**Developer Token:** ${tokenStored ? '✔ stored securely' : '✘ not set'}`,
        `**Manager Account ID:** ${loginCustomerId}`,
        `**Python Path:** ${pythonPath}`,
    ].join('\n\n');

    const action = await vscode.window.showInformationMessage(
        `Google Ads MCP\n\n${lines}`,
        { modal: true },
        'Reconfigure',
        'Install Dependencies'
    );

    if (action === 'Reconfigure') {
        await vscode.commands.executeCommand('googleAdsMcp.setup');
    } else if (action === 'Install Dependencies') {
        await vscode.commands.executeCommand('googleAdsMcp.installDeps');
    }
}
