import { describe, expect, it } from 'vitest'

import type { HZDRShot } from '../../types'
import { buildShotDayLabels } from '../filter'
import {
  countShotQuickFilters,
  shotDayOptions,
  shotMatchesStatusFilter,
} from '../shot-quick-filter'
import { previewableProductDataset, shotAddColumnPath } from '../shot-products'
import { isTableArrowKey, shouldIgnoreTableArrowKey } from '../shot-keyboard'

const shot = (number: number, day: string, status: string): HZDRShot => ({
  source_key: 'demo',
  shot_number: number,
  fired_at: `${day}T10:00:00Z`,
  shot_key: `demo:${day.replace(/-/g, '')}:${String(number).padStart(6, '0')}`,
  metadata: { status },
  events: [],
  data_products: [],
})

describe('shot quick filters', () => {
  const shots = [
    shot(1, '2026-07-01', 'processed'),
    shot(2, '2026-07-01', 'needs-review'),
    shot(3, '2026-07-02', 'needs-review'),
    shot(4, '2026-07-02', 'revision-needed'),
  ]
  const labels = buildShotDayLabels(shots)

  it('lists the days in order and counts each chip within the other', () => {
    const days = shotDayOptions(shots, labels)
    expect(days).toEqual(['Day 1', 'Day 2'])
    expect(
      countShotQuickFilters(shots, { status: 'all', day: '' }, labels, days)
    ).toEqual({
      status: { all: 4, 'needs-review': 2, 'revision-needed': 1 },
      allDays: 4,
      day: { 'Day 1': 2, 'Day 2': 2 },
    })
    expect(
      countShotQuickFilters(
        shots,
        { status: 'needs-review', day: 'Day 2' },
        labels,
        days
      )
    ).toEqual({
      status: { all: 2, 'needs-review': 1, 'revision-needed': 1 },
      allDays: 2,
      day: { 'Day 1': 1, 'Day 2': 1 },
    })
  })

  it('matches the status value exactly', () => {
    expect(shotMatchesStatusFilter(shots[1], 'needs-review')).toBe(true)
    expect(shotMatchesStatusFilter(shots[1], 'revision-needed')).toBe(false)
    expect(shotMatchesStatusFilter(shots[1], 'all')).toBe(true)
  })
})

describe('previewable data products', () => {
  const product = {
    source: 'LaserData',
    kind: 'hdf5_dataset',
    dataset_name: '/entry/laserdata/waveform/ev-1/values',
    preview_kind: 'line',
    shape: [256],
    metadata: {},
  }

  it('previews in-file scalars, lines and images, without the slash', () => {
    expect(previewableProductDataset(product)).toBe(
      'entry/laserdata/waveform/ev-1/values'
    )
    expect(
      previewableProductDataset({
        ...product,
        preview_kind: undefined,
        shape: [4, 4],
      })
    ).toBe('entry/laserdata/waveform/ev-1/values')
  })

  it('skips files, stacks and LabFrog tables of every shot', () => {
    expect(previewableProductDataset({ ...product, path: '/data/a.h5' })).toBe(
      undefined
    )
    expect(
      previewableProductDataset({ ...product, preview_kind: 'stack' })
    ).toBe(undefined)
    expect(
      previewableProductDataset({
        ...product,
        metadata: { shot_indexed: true },
      })
    ).toBe(undefined)
    expect(
      previewableProductDataset({ ...product, dataset_name: undefined })
    ).toBe(undefined)
  })

  it('links Context Builder with the dataset as the input', () => {
    const path = shotAddColumnPath('demo', 'entry/laserdata/a b/values')
    expect(path).toBe(
      '/source/demo/context-builder?add=hdf5:entry/laserdata/a%20b/values'
    )
    expect(new URL(path, 'http://x').searchParams.get('add')).toBe(
      'hdf5:entry/laserdata/a b/values'
    )
  })
})

describe('table arrow keys', () => {
  const key = (name: string, extra = {}) => ({
    key: name,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    ...extra,
  })

  it('moves on plain Up and Down only', () => {
    expect(isTableArrowKey(key('ArrowDown'))).toBe(true)
    expect(isTableArrowKey(key('ArrowUp'))).toBe(true)
    expect(isTableArrowKey(key('ArrowLeft'))).toBe(false)
    expect(isTableArrowKey(key('ArrowDown', { shiftKey: true }))).toBe(false)
    expect(isTableArrowKey(key('ArrowDown', { defaultPrevented: true }))).toBe(
      false
    )
  })

  it('leaves inputs, selects and comboboxes their own keys', () => {
    const input = document.createElement('input')
    const select = document.createElement('select')
    const combo = document.createElement('div')
    combo.setAttribute('role', 'combobox')
    const inside = document.createElement('span')
    combo.appendChild(inside)
    const button = document.createElement('button')
    expect(shouldIgnoreTableArrowKey(input)).toBe(true)
    expect(shouldIgnoreTableArrowKey(select)).toBe(true)
    expect(shouldIgnoreTableArrowKey(inside)).toBe(true)
    expect(shouldIgnoreTableArrowKey(button)).toBe(false)
    expect(shouldIgnoreTableArrowKey(null)).toBe(true)
  })
})
