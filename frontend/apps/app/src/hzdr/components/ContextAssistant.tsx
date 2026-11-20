import { useState } from 'react'
import {
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
} from '@mantine/core'
import { IconCheck, IconEye, IconEyeOff, IconPencil } from '@tabler/icons-react'

import type { ColumnSuggestion, HZDRShot } from '../types'
import { useDatasetPreview } from '../utils/columnPreview'

import { ColumnPreview } from './ColumnPreview'

/** Cards shown before "Show more": a short list reads, a long one is skipped. */
export const FIRST_SUGGESTIONS = 6

const ORIGIN_COLORS: Record<ColumnSuggestion['origin'], string> = {
  labfrog: 'green',
  metadata: 'blue',
  hdf5: 'violet',
}

/** The column a suggestion would add, on the example shot and the campaign. */
function SuggestionPreview({
  suggestion,
  sourceKey,
  exampleShot,
  shots,
}: {
  suggestion: ColumnSuggestion
  sourceKey?: string
  exampleShot?: HZDRShot
  shots: HZDRShot[]
}) {
  const hdf5 = suggestion.origin === 'hdf5'
  const dataset = useDatasetPreview(
    sourceKey,
    exampleShot,
    hdf5 ? (suggestion.previewName ?? suggestion.field) : undefined
  )
  return (
    <ColumnPreview
      title={suggestion.title}
      exampleShot={exampleShot}
      shots={shots}
      input={
        hdf5
          ? { source: 'hdf5', display: suggestion.display, ...dataset }
          : {
              source: 'metadata',
              field: suggestion.field,
              type: suggestion.type === 'text' ? 'text' : 'number',
              unit: suggestion.unit,
            }
      }
    />
  )
}

type PreviewContext = {
  sourceKey?: string
  exampleShot?: HZDRShot
  shots: HZDRShot[]
}

function SuggestionCard({
  suggestion,
  busy,
  previewContext,
  onAdd,
  onIgnore,
  onCustomise,
}: {
  suggestion: ColumnSuggestion
  busy: boolean
  previewContext: PreviewContext
  onAdd: () => void
  onIgnore: () => void
  onCustomise: () => void
}) {
  const [showCode, setShowCode] = useState(false)
  const [showPreview, setShowPreview] = useState(false)
  return (
    <Card
      withBorder
      radius={4}
      p="sm"
      aria-label={`Suggestion: ${suggestion.title}`}
      component="article"
    >
      <Stack gap={6}>
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Text fw={700} size="sm">
            {suggestion.title}
          </Text>
          <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
            {suggestion.starter ? (
              <Badge size="sm" radius={4} variant="filled" color="teal">
                Top pick
              </Badge>
            ) : null}
            <Badge
              size="sm"
              radius={4}
              variant="light"
              color={ORIGIN_COLORS[suggestion.origin]}
            >
              {suggestion.originLabel}
            </Badge>
          </Group>
        </Group>
        <Text size="xs">
          <Text span fw={600} size="xs">
            Why:{' '}
          </Text>
          {suggestion.reason}
        </Text>
        {suggestion.examples.length ? (
          <Text size="xs" c="dimmed">
            For example: {suggestion.examples.join(', ')}
          </Text>
        ) : null}
        <Group gap={6} align="flex-start" wrap="nowrap">
          <Text fw={600} size="xs" style={{ flexShrink: 0 }}>
            What it does:
          </Text>
          <Badge
            size="xs"
            radius={4}
            variant="outline"
            mt={1}
            style={{ flexShrink: 0 }}
            styles={{ label: { textTransform: 'none' } }}
          >
            {suggestion.displayLabel}
          </Badge>
          <Text size="xs">{suggestion.displayHelp}</Text>
        </Group>
        {showPreview ? (
          <SuggestionPreview suggestion={suggestion} {...previewContext} />
        ) : null}
        {showCode ? <Code block>{suggestion.block.trim()}</Code> : null}
        <Group gap="xs" mt={2}>
          <Button
            size="xs"
            leftSection={<IconCheck size={14} />}
            onClick={onAdd}
            disabled={busy}
          >
            Add this column
          </Button>
          <Button
            size="xs"
            variant="default"
            leftSection={<IconEyeOff size={14} />}
            onClick={onIgnore}
          >
            Ignore this suggestion
          </Button>
          <Button
            size="xs"
            variant="subtle"
            leftSection={<IconEye size={14} />}
            onClick={() => setShowPreview((shown) => !shown)}
            aria-expanded={showPreview}
          >
            {showPreview ? 'Hide preview' : 'Preview'}
          </Button>
          <Button
            size="xs"
            variant="subtle"
            leftSection={<IconPencil size={14} />}
            onClick={onCustomise}
          >
            Customise
          </Button>
          <Button
            size="xs"
            variant="subtle"
            color="gray"
            onClick={() => setShowCode((shown) => !shown)}
          >
            {showCode ? 'Hide code' : 'Show code'}
          </Button>
        </Group>
      </Stack>
    </Card>
  )
}

/**
 * Context Builder's "Help me": what is worth a column, why, and what the
 * column would do, one suggestion at a time.
 */
export function ContextAssistant({
  suggestions,
  loading,
  error,
  destination,
  busy,
  ignoredCount,
  addedCount,
  onAdd,
  onIgnore,
  onCustomise,
  onRestoreIgnored,
  sourceKey,
  exampleShot,
  shots = [],
}: {
  sourceKey?: string
  exampleShot?: HZDRShot
  shots?: HZDRShot[]
  suggestions: ColumnSuggestion[]
  loading: boolean
  error: string
  destination: string
  busy: boolean
  ignoredCount: number
  addedCount: number
  onAdd: (suggestion: ColumnSuggestion) => void
  onIgnore: (suggestion: ColumnSuggestion) => void
  onCustomise: (suggestion: ColumnSuggestion) => void
  onRestoreIgnored: () => void
}) {
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? suggestions : suggestions.slice(0, FIRST_SUGGESTIONS)
  const hidden = suggestions.length - shown.length
  return (
    <Paper withBorder radius={4} p="sm" bg="var(--mantine-color-teal-0)">
      <Stack gap="sm">
        <Stack gap={2}>
          <Text size="sm" fw={700}>
            Suggested columns
          </Text>
          <Text size="xs" c="dimmed">
            Picked from what this campaign’s shots record. Add the ones you want
            (each is saved to {destination} straight away), ignore the rest, or
            customise one in the builder first.
          </Text>
        </Stack>
        {loading ? (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="xs">Looking through the campaign’s records…</Text>
          </Group>
        ) : null}
        {error ? (
          <Text size="xs" c="red">
            {error}
          </Text>
        ) : null}
        {!loading && !suggestions.length ? (
          <Text size="sm">
            {addedCount || ignoredCount
              ? 'Nothing left to suggest: every suggestion is in your context file or ignored.'
              : 'Nothing to suggest yet: no recorded value varies from shot to shot. Use “Build a column myself” instead.'}
          </Text>
        ) : null}
        {shown.map((suggestion) => (
          <SuggestionCard
            key={suggestion.id}
            suggestion={suggestion}
            busy={busy}
            previewContext={{ sourceKey, exampleShot, shots }}
            onAdd={() => onAdd(suggestion)}
            onIgnore={() => onIgnore(suggestion)}
            onCustomise={() => onCustomise(suggestion)}
          />
        ))}
        {hidden > 0 ? (
          <Button variant="light" size="xs" onClick={() => setShowAll(true)}>
            Show {hidden} more suggestion{hidden === 1 ? '' : 's'}
          </Button>
        ) : null}
        <Group justify="space-between" gap="xs">
          <Text size="xs" c="dimmed">
            {suggestions.length} to review
            {addedCount ? ` · ${addedCount} already in ${destination}` : ''}
            {ignoredCount ? ` · ${ignoredCount} ignored` : ''}
          </Text>
          {ignoredCount ? (
            <Button size="xs" variant="subtle" onClick={onRestoreIgnored}>
              Show ignored suggestions again
            </Button>
          ) : null}
        </Group>
      </Stack>
    </Paper>
  )
}
