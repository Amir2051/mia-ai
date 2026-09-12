import { describe, it, expect, vi, beforeEach } from 'vitest';

const actualRunResponse = {
  data: {
    data: {
      import: {
        id: 1,
        status: 'completed',
        sync_status: 'completed',
        summary: {
          processed: 2,
          created: 1,
          failed: 0,
          skipped: 1,
          total: 2,
          valid: 1,
          errors: 1,
          duplicates: 0,
        },
        details: [
          { row: 1, status: 'created', mapped: { title: 'Test' } },
          { row: 2, status: 'skipped', error: ['title is required'] },
        ],
      },
    },
    connected: true,
  },
};

type RunImport = {
  id: number;
  status: string;
  sync_status: string;
  summary?: Record<string, number>;
  details?: any[];
};

function parseRunResponse(res: typeof actualRunResponse) {
  const runData = (res.data?.data ?? null) as { import?: RunImport } | null;
  const summary = runData?.import?.summary ?? {};
  return {
    runData,
    summary,
  };
}

describe('Imports run response contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('parses the actual backend /imports/{id}/run response shape without crashing', () => {
    const { runData, summary } = parseRunResponse(actualRunResponse);

    expect(runData).toEqual({
      import: {
        id: 1,
        status: 'completed',
        sync_status: 'completed',
        summary: {
          processed: 2,
          created: 1,
          failed: 0,
          skipped: 1,
          total: 2,
          valid: 1,
          errors: 1,
          duplicates: 0,
        },
        details: [
          { row: 1, status: 'created', mapped: { title: 'Test' } },
          { row: 2, status: 'skipped', error: ['title is required'] },
        ],
      },
    });

    expect(summary).toEqual({
      processed: 2,
      created: 1,
      failed: 0,
      skipped: 1,
      total: 2,
      valid: 1,
      errors: 1,
      duplicates: 0,
    });

    expect(summary.processed).toBe(2);
    expect(summary.created).toBe(1);
    expect(summary.failed).toBe(0);
    expect(summary.skipped).toBe(1);
  });

  it('does not throw when backend omits summary', () => {
    const response = {
      data: {
        data: {
          import: {
            id: 2,
            status: 'pending',
            sync_status: 'pending',
            details: [],
          },
        },
        connected: true,
      },
    };

    const { runData, summary } = parseRunResponse(response as any);

    expect(runData?.import?.id).toBe(2);
    expect(summary).toEqual({});
  });

  it('does not throw when backend omits import object', () => {
    const response = {
      data: {
        data: null,
        connected: true,
      },
    };

    const { runData, summary } = parseRunResponse(response as any);

    expect(runData).toBeNull();
    expect(summary).toEqual({});
  });
});
