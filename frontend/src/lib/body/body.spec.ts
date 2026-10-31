import { describe, expect, it } from 'vitest';
import { openapi } from '$lib/testing/openapi';
import { bodyGroups, composition } from './metrics';
import type { BloodPressure, BodySummary } from './types';

/** A reading in the API's own shape; the contract test below keeps it honest. */
const READING: BloodPressure = {
	avg_systolic_mmhg: 118,
	avg_diastolic_mmhg: 76,
	max_systolic_mmhg: null,
	max_diastolic_mmhg: null,
	min_systolic_mmhg: null,
	min_diastolic_mmhg: null,
	reading_count: 1
};

const body = (over: Partial<BodySummary['slow_changing']> = {}, latest = {}, averaged = {}) =>
	({
		source: {
			provider: 'garmin',
			source: null,
			device: null,
			device_type: null,
			device_name: null
		},
		slow_changing: {
			weight_kg: 74.3,
			height_cm: 181,
			body_fat_percent: null,
			muscle_mass_kg: 59.4,
			bmi: 22.7,
			age: 34,
			...over
		},
		averaged: {
			period_days: 7,
			resting_heart_rate_bpm: 54,
			avg_hrv_sdnn_ms: null,
			avg_hrv_rmssd_ms: 41.8,
			period_start: '',
			period_end: '',
			...averaged
		},
		latest: {
			body_temperature_celsius: null,
			body_temperature_measured_at: null,
			skin_temperature_celsius: null,
			skin_temperature_measured_at: null,
			blood_pressure: null,
			blood_pressure_measured_at: null,
			...latest
		}
	}) as BodySummary;

describe('composition', () => {
	// A fixed shape with dashes, unlike the detail columns: "no weight on record"
	// is one of the things somebody opens this tab to find out.
	it('keeps its four slots and dashes what is missing', () => {
		const shown = composition(body()).map((figure) => [figure.label, figure.value]);
		expect(shown).toEqual([
			['Weight', '74.3 kg'],
			['Height', '181 cm'],
			['Body fat', '-'],
			['BMI', '22.7']
		]);
	});

	it('drops a trailing zero rather than printing 74.0 kg', () => {
		expect(composition(body({ weight_kg: 74 }))[0].value).toBe('74 kg');
	});
});

describe('bodyGroups', () => {
	it('leaves out the groups nothing arrived for', () => {
		const titles = bodyGroups(body()).map((group) => group.title);
		expect(titles).toEqual(['Averaged over 7 days', 'Composition']);
	});

	// Withheld unless recent, so the reading is worthless without its timestamp.
	it('carries a recent reading together with when it was taken', () => {
		const groups = bodyGroups(
			body(
				{},
				{ body_temperature_celsius: 36.6, body_temperature_measured_at: '2026-09-20T07:00:00Z' }
			)
		);
		const recent = groups.find((group) => group.title === 'Recent readings');
		expect(recent?.fields.map((field) => field.label)).toEqual(['Body temp', 'Measured']);
	});

	it('reads a blood pressure as one figure, or not at all', () => {
		const withBp = bodyGroups(body({}, { blood_pressure: READING }));
		expect(withBp.find((group) => group.title === 'Blood pressure')?.fields[0].value).toBe(
			'118/76 mmHg'
		);
		expect(bodyGroups(body()).some((group) => group.title === 'Blood pressure')).toBe(false);
	});
});

// FORK (fix-blood-pressure-fields): the fixture once used the shape the frontend wanted, not
// the one the API sends, so the page rendered nothing while every test passed.
describe('BloodPressure contract', () => {
	it('matches the API schema in docs/openapi.json', () => {
		const schema = openapi().components.schemas.BloodPressure as { properties: object };
		expect(Object.keys(READING).sort()).toEqual(Object.keys(schema.properties).sort());
	});
});
