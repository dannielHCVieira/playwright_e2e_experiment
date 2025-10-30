// eslint-disable-next-line notice/notice
const fs = require('fs');
const path = require('path');

function stripAnsi(s) {
  return s.replace(/\x1B\[[0-9;]*m/g, '');
}

class CsvReporter {
  constructor(options) {
    this._options = options || {};
    this._suite = null;
    this._pendingWrite = Promise.resolve();
  }

  onBegin(config, suite) {
    this._suite = suite;
  }

  onEnd(result) {
    const rows = [['Test Name', 'Expected Status', 'Status', 'Error Message']];
    for (const project of this._suite.suites) {
      for (const file of project.suites) {
        for (const test of file.allTests()) {
          const fixme = test.annotations.find(a => a.type === 'fixme');
          if (test.ok() && !fixme)
            continue;
          const row = [];
          const [, , , ...titles] = test.titlePath();
          row.push(csvEscape(`${file.title} › ${titles.join(' › ')}`));
          row.push(test.expectedStatus);
          row.push(test.outcome());
          if (fixme) {
            row.push('fixme' + (fixme.description ? `: ${fixme.description.replace(/\s+/g, ' ')}` : ''));
          } else {
            const r = test.results.find(r => r.error);
            if (r) {
              const msg = stripAnsi((r.error?.message || '').replace(/\s+/g, ' ').trim().substring(0, 1024));
              row.push(csvEscape(msg));
            } else {
              const fail = test.annotations.find(a => a.type === 'fail');
              row.push(fail ? csvEscape(`Should have failed: ${fail.description || ''}`) : '');
            }
          }
          rows.push(row);
        }
      }
    }
    const csv = rows.map(r => r.join(',')).join('\n');
    const configDir = this._options.configDir || process.cwd();
    const outputFile = path.resolve(configDir, (this._options.outputFile || 'test-results.csv'));
    this._pendingWrite = (async () => {
      await fs.promises.mkdir(path.dirname(outputFile), { recursive: true });
      await fs.promises.writeFile(outputFile, csv);
    })();
  }

  async onExit() {
    await this._pendingWrite;
  }

  printsToStdio() {
    return false;
  }
}

function csvEscape(str) {
  if (str.includes('"') || str.includes(',') || str.includes('\n'))
    return `"${str.replace(/"/g, '""')}"`;
  return str;
}

module.exports = CsvReporter;


