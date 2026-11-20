import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'
import { MemoryRouter, Route, Routes } from 'react-router'

import { ContextBuilderPage } from '../ContextBuilderPage'

vi.mock('@damnit-frontend/ui', () => ({
  HomePage: ({ main }: { main: ReactNode }) => main,
}))
vi.mock('../../components/AppHeader', () => ({ AppHeader: () => null }))
vi.mock('../../components/previews', () => ({ VisualPreview: () => null }))

const base = '/metadata/hzdr/sources/radbio'
const filesBase = '/contextfile/campaign/radbio/me/files'
const chargeBlock = `from damnit_ctx import Skip, Variable


@Variable(title='ICT charge (nC)')
def ict_charge(run, value: 'meta#labfrog_variables.ICT charge.value' = None):
    return float(value)
`
const suggestions = [
  {
    id: 'labfrog:ICT charge',
    title: 'ICT charge (nC)',
    function: 'ict_charge',
    origin: 'labfrog',
    originLabel: 'LabFrog record',
    field: 'labfrog_variables.ICT charge.value',
    path: 'meta#labfrog_variables.ICT charge.value',
    type: 'number',
    unit: 'nC',
    count: 2,
    total: 2,
    examples: ['1,5', '3'],
    reason: 'Recorded on 2 of 2 shots, from 1.5 to 3 nC, 2 different values.',
    starter: true,
    block: chargeBlock,
    display: 'number',
    displayLabel: 'Number column',
    displayHelp: 'Shows the value in each shot’s row.',
  },
  {
    id: 'labfrog:Shot group',
    title: 'Shot group',
    function: 'shot_group',
    origin: 'labfrog',
    originLabel: 'LabFrog record',
    field: 'labfrog_variables.Shot group.value',
    path: 'meta#labfrog_variables.Shot group.value',
    type: 'text',
    unit: '',
    count: 2,
    total: 2,
    examples: ['A', 'B'],
    reason: 'Recorded on 2 of 2 shots as one of 2 labels: A (1), B (1).',
    starter: false,
    block: chargeBlock
      .replace('ICT charge (nC)', 'Shot group')
      .replace('ict_charge', 'shot_group')
      .replace('ICT charge.value', 'Shot group.value'),
    display: 'text',
    displayLabel: 'Label column',
    displayHelp: 'Shows the label.',
  },
]
const shots = [1, 2].map((number) => ({
  source_key: 'radbio',
  shot_number: number,
  fired_at: `2026-05-0${number}T08:15:00Z`,
  shot_key: `radbio:2026050${number}:00000${number}`,
  metadata: {
    labfrog_variables: {
      'ICT charge': { value: number === 1 ? '1,5' : '3', unit: 'nC' },
      'Shot group': { value: number === 1 ? 'A' : 'B' },
    },
  },
  events: [],
  data_products: [],
}))

function setup() {
  const saved: Record<string, string> = {}
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      const { fileContent } = JSON.parse(String(init.body)) as {
        fileContent: string
      }
      saved[url] = fileContent
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ fileContent, path: url }),
      })
    }
    const value =
      url === `${base}/shots`
        ? shots
        : url === base
          ? { key: 'radbio', title: 'Radbio' }
          : url.endsWith('/assist')
            ? { campaign: 'radbio', suggestions }
            : url.endsWith('/files')
              ? [{ name: 'context.py', active: true }]
              : url.includes('/shots/')
                ? {
                    shot: shots[0],
                    hdf5_datasets: [
                      {
                        name: 'cam/image',
                        shape: [64, 64],
                        dtype: 'uint16',
                      },
                    ],
                    hdf5_exists: true,
                  }
                : {
                    fileContent:
                      'from damnit_ctx import Variable\n\n\n@Variable(title="Existing")\ndef existing(run):\n    return 1\n',
                  }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(value) })
  })
  vi.stubGlobal('fetch', fetchMock)
  render(
    <MantineProvider>
      <MemoryRouter initialEntries={['/source/radbio/context-builder']}>
        <Routes>
          <Route
            path="/source/:source_key/context-builder"
            element={<ContextBuilderPage />}
          />
        </Routes>
      </MemoryRouter>
    </MantineProvider>
  )
  return { fetchMock, saved }
}

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
  Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
})

const card = (title: string) =>
  screen.findByRole('article', { name: `Suggestion: ${title}` })

describe('Context Builder "Help me choose"', () => {
  it('explains each suggestion, including datasets on the example shot', async () => {
    setup()
    const charge = await card('ICT charge (nC)')
    expect(within(charge).getByText('Top pick')).toBeInTheDocument()
    expect(
      within(charge).getByText(/Recorded on 2 of 2 shots, from 1.5 to 3 nC/)
    ).toBeInTheDocument()
    expect(within(charge).getByText('Number column')).toBeInTheDocument()
    const image = await card('Image')
    expect(within(image).getByText('Image preview')).toBeInTheDocument()
    expect(within(image).getByText(/On the example shot/)).toBeInTheDocument()
  })

  it('adds a suggestion to the context file, which then drops it', async () => {
    const user = userEvent.setup()
    const { saved } = setup()
    const charge = await card('ICT charge (nC)')
    await user.click(
      within(charge).getByRole('button', { name: 'Add this column' })
    )
    await waitFor(() => expect(saved[`${filesBase}/context.py`]).toBeDefined())
    const content = saved[`${filesBase}/context.py`]
    expect(content).toContain('def existing(run)')
    expect(content).toContain(
      "def ict_charge(run, value: 'meta#labfrog_variables.ICT charge.value' = None):"
    )
    // The block's imports land at the top, before any column, so Skip is
    // defined for it (mergeImportsAtTop, as for "Add to context").
    const importLine = content.indexOf('from damnit_ctx import Skip, Variable')
    expect(importLine).toBeGreaterThanOrEqual(0)
    expect(importLine).toBeLessThan(content.indexOf('@Variable'))
    await waitFor(() =>
      expect(
        screen.queryByRole('article', { name: 'Suggestion: ICT charge (nC)' })
      ).not.toBeInTheDocument()
    )
    expect(screen.getByText(/1 already in context.py/)).toBeInTheDocument()
  })

  it('ignores a suggestion until it is asked for again', async () => {
    const user = userEvent.setup()
    const { saved } = setup()
    const group = await card('Shot group')
    await user.click(
      within(group).getByRole('button', { name: 'Ignore this suggestion' })
    )
    expect(
      screen.queryByRole('article', { name: 'Suggestion: Shot group' })
    ).not.toBeInTheDocument()
    expect(saved).toEqual({})
    expect(
      window.localStorage.getItem('hzdr.contextAssist.ignored.radbio')
    ).toBe(JSON.stringify(['labfrog:Shot group']))
    await user.click(
      screen.getByRole('button', { name: 'Show ignored suggestions again' })
    )
    expect(await card('Shot group')).toBeInTheDocument()
  })

  it('customises a suggestion in the builder form', async () => {
    const user = userEvent.setup()
    setup()
    const group = await card('Shot group')
    await user.click(within(group).getByRole('button', { name: 'Customise' }))
    expect(screen.getByRole('textbox', { name: 'Column title' })).toHaveValue(
      'Shot group'
    )
    expect(screen.getByRole('textbox', { name: 'Data to use' })).toHaveValue(
      'Shot group (2/2 shots)'
    )
  })
})
