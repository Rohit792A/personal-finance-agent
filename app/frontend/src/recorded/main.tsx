/**
 * Entry point of the recorded demo build (`npm run build:recorded`): vite.config.ts points index.html
 * here instead of src/main.tsx, so Firebase sign-in and the backend URLs never enter this bundle.
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../styles.css';
import { RecordedApp } from './RecordedApp';

const container = document.getElementById('root');
if (!container) throw new Error('Missing #root element in index.html');

createRoot(container).render(
  <StrictMode>
    <RecordedApp />
  </StrictMode>,
);
