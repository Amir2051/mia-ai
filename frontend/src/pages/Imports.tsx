import { useCallback, useEffect, useRef, useState } from 'react';
import api from '../services/api';

type ImportRow = {
  id?: number | null;
  source?: string | null;
  supplier_sku?: string | null;
  title?: string | null;
  status?: string | null;
  sync_status?: string | null;
  error?: string | null;
  shopify_product_id?: string | null;
  created_at?: string | null;
  summary?: {
    processed?: number | null;
    created?: number | null;
    failed?: number | null;
    skipped?: number | null;
    total?: number | null;
    valid?: number | null;
    warnings?: number | null;
    errors?: number | null;
    duplicates?: number | null;
  };
};

type PreviewValidation = {
  valid?: Array<{ row: number; mapped: Record<string, string> }>;
  warnings?: Array<{ row: number; warnings: string[]; mapped: Record<string, string> }>;
  errors?: Array<{ row: number; errors: string[]; mapped: Record<string, string> }>;
  duplicates?: Array<{ row: number; type: string; value: string }>;
  summary?: {
    total?: number;
    valid?: number;
    warnings?: number;
    errors?: number;
    duplicates?: number;
  };
};

type ImportRecord = {
  id?: number | null;
  status?: string | null;
  sync_status?: string | null;
  summary?: {
    processed?: number | null;
    created?: number | null;
    failed?: number | null;
    skipped?: number | null;
    total?: number | null;
    valid?: number | null;
    warnings?: number | null;
    errors?: number | null;
    duplicates?: number | null;
  };
  details?: Array<Record<string, unknown>>;
};

type PreviewResponse = {
  import: ImportRecord;
};

type RunResponse = {
  import: ImportRecord;
};

type Step = 'upload' | 'configure' | 'preview' | 'results';

const MAX_FILE_SIZE = 5 * 1024 * 1024;
const ACCEPTED_TYPES = ['.csv', 'text/csv'];

export default function Imports() {
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [imports, setImports] = useState<ImportRow[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedImport, setSelectedImport] = useState<ImportRow | null>(null);
  const [step, setStep] = useState<Step>('upload');

  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [fileName, setFileName] = useState('');
  const [fileContent, setFileContent] = useState('');
  const [parsedRows, setParsedRows] = useState<Array<Record<string, string>>>([]);
  const [parsedColumns, setParsedColumns] = useState<string[]>([]);
  const [preview, setPreview] = useState<PreviewValidation | null>(null);
  const [importConfig, setImportConfig] = useState({
    title: '',
    duplicate_action: 'skip',
    status: 'DRAFT',
    description: '',
    supplier: '',
    category: '',
    tags: '',
  });
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [currentImport, setCurrentImport] = useState<PreviewResponse | RunResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ processed: 0, created: 0, failed: 0, skipped: 0 });

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get('/auth/session')
      .then((res) => setConnected(!!res.data?.connected))
      .catch(() => setConnected(false));
  }, []);

  useEffect(() => {
    if (!connected) {
      setImports([]);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    api.get('/imports/')
      .then((res) => {
        if (cancelled) return;
        const rows = (res.data?.data?.imports ?? []) as ImportRow[];
        setImports(rows);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setImports([]);
          const message = (err as Error)?.message || 'Failed to load imports';
          setError(typeof message === 'string' ? message : JSON.stringify(message));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [connected]);

  useEffect(() => {
    if (!selectedId) {
      setSelectedImport(null);
      return;
    }

    let cancelled = false;
    setLoading(true);

    api.get(`/imports/${selectedId}`)
      .then((res) => {
        if (cancelled) return;
        setSelectedImport((res.data?.data) as ImportRow | null);
      })
      .catch(() => {
        if (!cancelled) setSelectedImport(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const resetWorkflow = () => {
    setFile(null);
    setFileError(null);
    setFileName('');
    setParsedRows([]);
    setParsedColumns([]);
    setPreview(null);
    setImportConfig({
      title: '',
      duplicate_action: 'skip',
      status: 'DRAFT',
      description: '',
      supplier: '',
      category: '',
      tags: '',
    });
    setMapping({});
    setCurrentImport(null);
    setProgress({ processed: 0, created: 0, failed: 0, skipped: 0 });
    setStep('upload');
  };

  const readFile = useCallback((inputFile: File) => {
    if (inputFile.size > MAX_FILE_SIZE) {
      setFileError('File size must be under 5MB.');
      return;
    }

    const reader = new FileReader();
    reader.onload = (event) => {
      const content = event.target?.result;
      if (typeof content !== 'string') {
        setFileError('Unable to read file contents.');
        return;
      }

      const lines = content.split(/\r?\n/).filter((line) => line.trim().length > 0);
      if (lines.length < 2) {
        setFileError('CSV must include a header row and at least one data row.');
        return;
      }

      const columns = lines[0].split(',').map((column) => column.trim()).filter(Boolean);
      const rows = lines.slice(1).map((line) => {
        const values = line.split(',');
        const row: Record<string, string> = {};
        columns.forEach((column, index) => {
          row[column] = values[index]?.trim() ?? '';
        });
        return row;
      });

      setFile(inputFile);
      setFileName(inputFile.name);
      setFileContent(content);
      setParsedRows(rows.slice(0, 20));
      setParsedColumns(columns);

      // Infer CSV -> Shopify product field mapping from common header names.
      const aliases: Record<string, string[]> = {
        title: ['title', 'product_title', 'name'],
        descriptionHtml: ['description', 'body_html', 'description_html', 'body'],
        vendor: ['vendor', 'supplier', 'brand'],
        productType: ['product_type', 'category', 'type'],
        tags: ['tags', 'tag'],
        sku: ['sku', 'variant_sku', 'variant sku'],
        price: ['price', 'variant_price', 'variant price'],
        compareAtPrice: ['compare_at_price', 'variant_compare_at_price', 'compare at price'],
        inventoryQuantity: ['inventory_quantity', 'variant_inventory_qty', 'inventory_qty', 'quantity'],
        weight: ['weight', 'variant_weight'],
      };
      const inferred: Record<string, string> = {};
      const normalized = (value: string) => value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_');
      columns.forEach((column) => {
        const n = normalized(column);
        for (const [target, candidates] of Object.entries(aliases)) {
          if (candidates.some((candidate) => normalized(candidate) === n)) {
            inferred[target] = column;
            break;
          }
        }
      });
      setMapping(inferred);
      setFileError(null);
    };

    reader.readAsText(inputFile);
  }, []);

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const inputFile = event.target.files?.[0];
    if (!inputFile) return;
    readFile(inputFile);
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const inputFile = event.dataTransfer.files?.[0];
    if (!inputFile) return;
    readFile(inputFile);
  };

  const updateConfig = <Key extends keyof typeof importConfig>(key: Key, value: (typeof importConfig)[Key]) => {
    setImportConfig((prev) => ({ ...prev, [key]: value }));
  };

  const normalizeTags = (tags: string): string[] =>
    tags.split(',')
      .map((tag) => tag.trim())
      .filter((tag) => tag.length > 0);

  const handlePreview = async () => {
    if (!fileContent) {
      setError('Upload a CSV before previewing.');
      return;
    }

    const previewBody = {
      content: fileContent,
      title: importConfig.title,
      description: importConfig.description,
      supplier: importConfig.supplier,
      category: importConfig.category,
      tags: normalizeTags(importConfig.tags || ''),
      status: importConfig.status,
      duplicate_action: importConfig.duplicate_action,
    };

    setError(null);
    try {
      const res = await api.post('/imports/preview', previewBody);
      const importId = res.data?.data?.import?.id;
      if (importId) {
        setCurrentImport({
          import: {
            id: importId,
            status: res.data.data.import.status ?? null,
            sync_status: res.data.data.import.sync_status ?? null,
            summary: res.data.data.import.summary,
            details: res.data.data.import.details,
          },
        } as any);
      } else {
        setCurrentImport(null);
      }
      setStep('preview');
    } catch (err) {
      const axiosError = err as any;
      const status = axiosError?.response?.status;
      const data = axiosError?.response?.data;
      const detail = data?.detail ?? data?.error ?? data?.message;
      let message = (axiosError?.message && !status ? String(axiosError.message) : undefined) ?? detail ?? JSON.stringify(data ?? err) ?? 'Preview failed';
      if (typeof message !== 'string') {
        message = JSON.stringify(message);
      }
      setError(String(message));
    }
  };

  const handleRun = async () => {
    if (!currentImport?.import?.id) {
      setError('Missing import. Please preview the CSV first.');
      return;
    }

    setRunning(true);
    setProgress({ processed: 0, created: 0, failed: 0, skipped: 0 });
    setError(null);

    try {
      const runPayload: Record<string, unknown> = {
        mapping,
        status: importConfig.status,
        duplicate_action: importConfig.duplicate_action,
      };

      if (!runPayload['content']) {
        const previewContent = (preview && typeof preview === 'object' && 'content' in preview ? (preview as any).content : null) || fileContent || '';
        runPayload['content'] = previewContent;
      }

      const res = await api.post(`/imports/${currentImport.import.id}/run`, runPayload);
      const runData = (res.data?.data ?? null) as RunResponse | null;
      const summary = runData?.import?.summary ?? {};
      setCurrentImport(runData);
      setProgress({
        processed: summary.processed ?? 0,
        created: summary.created ?? 0,
        failed: summary.failed ?? 0,
        skipped: summary.skipped ?? 0,
      });
      setStep('results');
    } catch (err) {
      const axiosError = err as any;
      const status = axiosError?.response?.status;
      const data = axiosError?.response?.data;
      const detail = data?.detail ?? data?.error ?? data?.message;
      let message = (axiosError?.message && !status ? String(axiosError.message) : undefined) ?? detail ?? JSON.stringify(data ?? err) ?? 'Import run failed';
      if (typeof message !== 'string') {
        message = JSON.stringify(message);
      }
      setError(String(message));
    } finally {
      setRunning(false);
    }
  };

  const renderPreview = () => {
    const validation = preview || currentImport?.import || null;
    const summary = validation?.summary || currentImport?.import?.summary;

    return (
      <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>Preview</h2>
        {summary && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
            <div style={{ padding: 12, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#616161' }}>Total rows</div>
              <div style={{ fontWeight: 600 }}>{summary.total ?? '—'}</div>
            </div>
            <div style={{ padding: 12, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#616161' }}>Valid rows</div>
              <div style={{ fontWeight: 600 }}>{summary.valid ?? '—'}</div>
            </div>
            <div style={{ padding: 12, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#d72c0d' }}>Errors</div>
              <div style={{ fontWeight: 600 }}>{summary.errors ?? '—'}</div>
            </div>
            <div style={{ padding: 12, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <div style={{ fontSize: 12, color: '#616161' }}>Duplicates</div>
              <div style={{ fontWeight: 600 }}>{summary.duplicates ?? '—'}</div>
            </div>
          </div>
        )}

        <h3 style={{ fontSize: 14, marginBottom: 8 }}>Sample rows</h3>
        {parsedRows.length === 0 && <p style={{ color: '#616161' }}>No rows parsed.</p>}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr>
                {parsedColumns.map((column) => (
                  <th key={column} style={{ textAlign: 'left', padding: 8, borderBottom: '1px solid #e1e3e5' }}>
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {parsedRows.slice(0, 5).map((row, index) => (
                <tr key={index}>
                  {parsedColumns.map((column) => (
                    <td key={column} style={{ padding: 8, borderBottom: '1px solid #f1f2f4' }}>
                      {row[column] || '—'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  return (
    <div>
      <h1 style={{ fontSize: 24, marginBottom: 12 }}>Import products</h1>

      {!connected && <p style={{ color: '#616161' }}>Connect Shopify to enable imports.</p>}
      {error && <p style={{ color: '#d72c0d' }}>Error: {error}</p>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Create import</h2>

          {step === 'upload' && (
            <div>
              <div
                onDrop={handleDrop}
                onDragOver={(event) => event.preventDefault()}
                onClick={() => fileInputRef.current?.click()}
                style={{
                  padding: 24,
                  border: '1px dashed #c9cccf',
                  borderRadius: 8,
                  background: '#fafafa',
                  cursor: 'pointer',
                }}
              >
                <p style={{ marginBottom: 8 }}>Drag and drop a CSV file here</p>
                <p style={{ color: '#616161', fontSize: 12 }}>or click to browse</p>
                <p style={{ color: '#616161', fontSize: 12 }}>Accepted: CSV, max 5MB</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPTED_TYPES.join(',')}
                  onChange={handleFileChange}
                  style={{ display: 'none' }}
                />
              </div>

              {fileName && <p style={{ marginTop: 8 }}>Selected: {fileName}</p>}
              {fileError && <p style={{ color: '#d72c0d' }}>{fileError}</p>}

              {file && (
                <div style={{ marginTop: 16 }}>
                  <h3 style={{ fontSize: 14, marginBottom: 8 }}>Import configuration</h3>
                  <div style={{ display: 'grid', gap: 8 }}>
                    <input
                      value={importConfig.title}
                      onChange={(event) => updateConfig('title', event.target.value)}
                      placeholder="Import name"
                      style={{ padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                    />
                    <select
                      value={importConfig.duplicate_action}
                      onChange={(event) => updateConfig('duplicate_action', event.target.value)}
                      style={{ padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                    >
                      <option value="skip">Skip duplicates</option>
                      <option value="update">Update duplicates</option>
                    </select>
                    <select
                      value={importConfig.status}
                      onChange={(event) => updateConfig('status', event.target.value)}
                      style={{ padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                    >
                      <option value="DRAFT">Draft</option>
                      <option value="ACTIVE">Active</option>
                    </select>
                    <input
                      value={importConfig.description}
                      onChange={(event) => updateConfig('description', event.target.value)}
                      placeholder="Description"
                      style={{ padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                    />
                    <input
                      value={importConfig.supplier}
                      onChange={(event) => updateConfig('supplier', event.target.value)}
                      placeholder="Supplier"
                      style={{ padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                    />
                    <input
                      value={importConfig.category}
                      onChange={(event) => updateConfig('category', event.target.value)}
                      placeholder="Category"
                      style={{ padding: 8, border: '1px solid #e1e3e5', borderRadius: 6 }}
                    />
                  </div>

                  <button
                    onClick={handlePreview}
                    style={{
                      marginTop: 16,
                      padding: '10px 16px',
                      border: '1px solid #e1e3e5',
                      borderRadius: 6,
                      background: 'white',
                      cursor: 'pointer',
                    }}
                  >
                    Continue to preview
                  </button>
                </div>
              )}
            </div>
          )}

          {step === 'preview' && (
            <div>
              {renderPreview()}
              <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                <button
                  onClick={() => setStep('upload')}
                  style={{ padding: '10px 16px', border: '1px solid #e1e3e5', borderRadius: 6, background: 'white', cursor: 'pointer' }}
                >
                  Back
                </button>
                <button
                  onClick={handleRun}
                  disabled={running}
                  style={{
                    padding: '10px 16px',
                    border: 'none',
                    borderRadius: 6,
                    background: running ? '#8c9196' : '#008060',
                    color: 'white',
                    cursor: running ? 'not-allowed' : 'pointer',
                    fontWeight: 600,
                  }}
                >
                  {running ? 'Running…' : 'Start import'}
                </button>
              </div>
            </div>
          )}

          {step === 'results' && (
            <div>
              <h2 style={{ fontSize: 16, marginBottom: 8 }}>Import results</h2>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                <div style={{ padding: 12, border: '1px solid #e1e3e5', borderRadius: 8 }}>
                  <div style={{ fontSize: 12, color: '#616161' }}>Processed</div>
                  <div style={{ fontWeight: 600 }}>{progress.processed}</div>
                </div>
                <div style={{ padding: 12, border: '1px solid #e1e3e5', borderRadius: 8 }}>
                  <div style={{ fontSize: 12, color: '#008060' }}>Successful</div>
                  <div style={{ fontWeight: 600 }}>{progress.created}</div>
                </div>
                <div style={{ padding: 12, border: '1px solid #e1e3e5', borderRadius: 8 }}>
                  <div style={{ fontSize: 12, color: '#d72c0d' }}>Failed</div>
                  <div style={{ fontWeight: 600 }}>{progress.failed}</div>
                </div>
                <div style={{ padding: 12, border: '1px solid #e1e3e5', borderRadius: 8 }}>
                  <div style={{ fontSize: 12, color: '#616161' }}>Skipped</div>
                  <div style={{ fontWeight: 600 }}>{progress.skipped}</div>
                </div>
              </div>
              <button
                onClick={resetWorkflow}
                style={{
                  padding: '10px 16px',
                  border: '1px solid #e1e3e5',
                  borderRadius: 6,
                  background: 'white',
                  cursor: 'pointer',
                }}
              >
                New import
              </button>
            </div>
          )}
        </div>

        <div style={{ border: '1px solid #e1e3e5', borderRadius: 8, padding: 16 }}>
          <h2 style={{ fontSize: 16, marginBottom: 8 }}>Import history</h2>
          {loading && <p>Loading...</p>}
          {!loading && imports.length === 0 && <p style={{ color: '#616161' }}>No imports yet.</p>}
          <div style={{ display: 'grid', gap: 8 }}>
            {imports.map((item) => (
              <div
                key={item.id}
                onClick={() => setSelectedId(item.id || null)}
                style={{
                  padding: 12,
                  border: '1px solid #e1e3e5',
                  borderRadius: 6,
                  background: selectedId === item.id ? '#f1f2f4' : 'white',
                  cursor: 'pointer',
                }}
              >
                <div style={{ fontWeight: 600 }}>{item.title || 'Untitled import'}</div>
                <div style={{ color: '#616161', fontSize: 12 }}>{item.supplier_sku || '—'} · {item.status || '—'} · {item.source || 'manual'}</div>
                <div style={{ color: '#616161', fontSize: 12 }}>{item.created_at || ''}</div>
                {item.error && <div style={{ color: '#d72c0d', fontSize: 12 }}>{item.error}</div>}
              </div>
            ))}
          </div>

          {selectedImport && (
            <div style={{ marginTop: 16, padding: 16, border: '1px solid #e1e3e5', borderRadius: 8 }}>
              <h2 style={{ fontSize: 16, marginBottom: 8 }}>Import details</h2>
              <div style={{ color: '#616161' }}>ID: {selectedImport.id ?? '—'}</div>
              <div style={{ color: '#616161' }}>Status: {selectedImport.status || '—'}</div>
              <div style={{ color: '#616161' }}>Sync status: {selectedImport.sync_status || '—'}</div>
              <div style={{ color: '#616161' }}>Created: {selectedImport.created_at || '—'}</div>
              <div style={{ color: '#616161' }}>Shopify product: {selectedImport.shopify_product_id || '—'}</div>
              {selectedImport.summary && (
                <div style={{ color: '#616161', marginTop: 8 }}>
                  Processed: {selectedImport.summary.processed ?? 0} · Created: {selectedImport.summary.created ?? 0} · Failed: {selectedImport.summary.failed ?? 0} · Skipped: {selectedImport.summary.skipped ?? 0}
                </div>
              )}
              {selectedImport.error && <div style={{ color: '#d72c0d', marginTop: 8 }}>{selectedImport.error}</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
