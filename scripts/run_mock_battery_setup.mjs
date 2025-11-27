import { chromium } from '@playwright/test';
import { runScriptSetups } from './dist/pre_nova_setup.js';

// Uso: node scripts/run_mock_battery_setup.mjs [arquivo-exato]
// Exemplo: node scripts/run_mock_battery_setup.mjs show-battery-status.spec.js
const exactFile = process.argv[2];

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  const options = {
    page,
    logger: console.log,
  };

  if (exactFile) {
    console.log(`Filtrando por arquivo: ${exactFile}`);
    options.exactFile = exactFile;
  } else {
    console.log('Nenhum arquivo especificado. Rodando todos os setups de mock-battery (pode dar conflito).');
    options.fileIncludes = 'examples/mock-battery/tests';
  }

  await runScriptSetups(options);

  console.log('Setup concluído com sucesso!');
  await browser.close();
})();