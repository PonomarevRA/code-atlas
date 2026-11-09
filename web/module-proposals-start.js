/** Bind the opt-in panel without executing analysis on navigation. */
import {ModuleProposalsPanel} from './module-proposals-page.js';

const panel = new ModuleProposalsPanel(document.querySelector('#module-proposals'), document.querySelector('#module-form'));
window.addEventListener('pagehide', () => panel.dispose(), {once: true});
