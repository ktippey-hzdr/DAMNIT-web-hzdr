import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MantineProvider } from '@mantine/core'

import type { ColumnSuggestion } from '../../types'
import { ContextAssistant, FIRST_SUGGESTIONS } from '../ContextAssistant'

afterEach(cleanup)

const suggestions: ColumnSuggestion[] = Array.from(
  { length: FIRST_SUGGESTIONS + 2 },
  (_, index) => ({
    id: `labfrog:v${index}`,
    title: `Value ${index}`,
    function: `v${index}`,
    origin: 'labfrog',
    originLabel: 'LabFrog record',
    field: `labfrog_variables.v${index}.value`,
    path: `meta#labfrog_variables.v${index}.value`,
    type: 'number',
    unit: '',
    count: 1,
    total: 1,
    examples: [],
    reason: 'Recorded on 1 of 1 shots',
    starter: index === 0,
    block: '',
    display: 'number',
    displayLabel: 'Number column',
    displayHelp: 'Shows the value.',
  })
)

describe('ContextAssistant', () => {
  it('shows the first few suggestions, then the rest on request', async () => {
    const user = userEvent.setup()
    render(
      <MantineProvider>
        <ContextAssistant
          suggestions={suggestions}
          loading={false}
          error=""
          destination="context.py"
          busy={false}
          ignoredCount={0}
          addedCount={0}
          onAdd={vi.fn()}
          onIgnore={vi.fn()}
          onCustomise={vi.fn()}
          onRestoreIgnored={vi.fn()}
        />
      </MantineProvider>
    )
    expect(screen.getAllByRole('article')).toHaveLength(FIRST_SUGGESTIONS)
    await user.click(
      screen.getByRole('button', { name: 'Show 2 more suggestions' })
    )
    expect(screen.getAllByRole('article')).toHaveLength(FIRST_SUGGESTIONS + 2)
  })
})
